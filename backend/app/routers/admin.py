"""Admin API, called only by the dashboard's server (BFF). Bearer API_ADMIN_TOKEN required."""

import asyncio
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import String, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import login_throttle
from app.api_models import (
    AccountOut,
    CampaignOut,
    CampaignPatch,
    CampaignStats,
    DayStats,
    DeliveryCounts,
    DeliveryOut,
    DeliveryPage,
    LimitsOut,
    MatchTestIn,
    MatchTestOut,
    OpsEventOut,
    PostOut,
    ReconcileOut,
    RetryIn,
    StatusOut,
    ThrottleKeyIn,
    ThrottleOut,
    TokenIn,
    WebhookEventOut,
)
from app.campaigns import (
    GATE_MESSAGE_MAX_CHARS,
    KEYWORD_MAX_CHARS,
    LINK_MAX_CHARS,
    MAX_KEYWORDS,
    MAX_PUBLIC_REPLIES,
    NAME_MAX_CHARS,
    PUBLIC_REPLY_MAX_CHARS,
    CampaignInput,
    create_campaign,
    duplicate_campaign,
    list_campaigns,
    set_campaign_state,
    update_campaign,
)
from app.db import utcnow
from app.errors import ApiError, invalid
from app.matching import KeywordMatcher
from app.meta.client import SUBSCRIBED_FIELDS, MetaClient
from app.meta.errors import MetaError
from app.meta.limits import BUTTON_TITLE_MAX_CHARS, MESSAGE_TEXT_MAX_CHARS
from app.meta.signature import tokens_equal
from app.models import (
    DM_JOB_TYPES,
    Account,
    AccountStatus,
    Campaign,
    Delivery,
    DeliveryStatus,
    Job,
    JobStatus,
    JobType,
    OpsEvent,
    Trigger,
    WebhookEvent,
)
from app.ops import OpsLevel, OpsSource, record
from app.reconciler import reconcile
from app.routers.public import SessionFactoryDep, SettingsDep
from app.status import collect_status, summarize_webhook_payload
from app.tokens import (
    DifferentAccountError,
    TokenDecryptError,
    clean_pasted_token,
    load_token,
    set_account_token,
)


def require_admin(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    expected = settings.api_admin_token.get_secret_value()
    if scheme.lower() != "bearer" or not tokens_equal(token, expected):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})


def get_meta(request: Request) -> MetaClient:
    meta: MetaClient = request.app.state.meta
    return meta


MetaDep = Annotated[MetaClient, Depends(get_meta)]
SessionFactory = async_sessionmaker[AsyncSession]

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])


def _not_found(what: str) -> ApiError:
    return ApiError(404, f"{what} not found")


def _aware(value: datetime | None, name: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ApiError(422, f"{name} needs a timezone (e.g. 2026-10-02T00:00:00Z)")
    return value


async def _stored_token(sf: SessionFactory, settings: SettingsDep) -> str:
    async with sf() as session:
        try:
            loaded = await load_token(session, settings)
        except TokenDecryptError:
            raise ApiError(409, "stored token cannot be decrypted: paste it again") from None
    if loaded is None:
        raise ApiError(409, "no token stored yet")
    return loaded[1]


# ---- status and account ------------------------------------------------------------------


@router.get("/status")
async def status(settings: SettingsDep, session_factory: SessionFactoryDep) -> StatusOut:
    return StatusOut.model_validate(await collect_status(session_factory, settings))


async def _account_out(sf: SessionFactory) -> AccountOut | None:
    async with sf() as session:
        account = await session.get(Account, 1)
    return None if account is None else AccountOut.model_validate(account, from_attributes=True)


@router.get("/account")
async def get_account(session_factory: SessionFactoryDep) -> AccountOut | None:
    return await _account_out(session_factory)


@router.put("/account/token")
async def put_token(
    body: TokenIn, settings: SettingsDep, session_factory: SessionFactoryDep, meta: MetaDep
) -> AccountOut:
    token = clean_pasted_token(body.token.get_secret_value())
    if not token:
        raise ApiError(422, "empty token")
    try:
        await set_account_token(session_factory, settings, meta, token)
    except MetaError as exc:
        raise ApiError(422, f"Meta refused the token: {exc}") from None
    except DifferentAccountError as exc:
        raise ApiError(409, str(exc)) from None
    account = await _account_out(session_factory)
    assert account is not None  # noqa: S101
    return account


@router.post("/account/subscribe")
async def subscribe(
    settings: SettingsDep, session_factory: SessionFactoryDep, meta: MetaDep
) -> AccountOut:
    token = await _stored_token(session_factory, settings)
    try:
        await meta.subscribe(token, SUBSCRIBED_FIELDS)
    except MetaError as exc:
        raise ApiError(502, f"subscribe failed: {exc}") from None
    async with session_factory() as session, session.begin():
        account = await session.get(Account, 1)
        if account is not None:
            account.webhook_subscribed_at = utcnow()
            record(session, OpsSource.WEBHOOK, OpsLevel.INFO, "webhook fields subscribed")
    out = await _account_out(session_factory)
    assert out is not None  # noqa: S101
    return out


async def _set_paused(sf: SessionFactory, paused: bool) -> AccountOut:
    async with sf() as session, session.begin():
        result = await session.execute(
            update(Account).where(Account.id == 1).values(sending_paused=paused)
        )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise ApiError(409, "no account yet: store a token first")
    out = await _account_out(sf)
    assert out is not None  # noqa: S101
    return out


@router.post("/account/pause")
async def pause_sending(session_factory: SessionFactoryDep) -> AccountOut:
    """Kill switch. Jobs wait; after resume they leave within 5 minutes."""
    return await _set_paused(session_factory, True)


@router.post("/account/resume")
async def resume_sending(session_factory: SessionFactoryDep) -> AccountOut:
    return await _set_paused(session_factory, False)


@router.get("/posts")
async def posts(
    settings: SettingsDep,
    session_factory: SessionFactoryDep,
    meta: MetaDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
) -> list[PostOut]:
    token = await _stored_token(session_factory, settings)
    try:
        media = await meta.list_media(token, limit=limit)
    except MetaError as exc:
        raise ApiError(502, f"could not list posts: {exc}") from None
    return [
        PostOut.model_validate({k: str(v) for k, v in item.items() if v is not None})
        for item in media
        if item.get("id")
    ]


# ---- campaigns ---------------------------------------------------------------------------


async def _counts(
    session: AsyncSession, campaign_id: int | None = None
) -> dict[int, DeliveryCounts]:
    query = select(Delivery.campaign_id, Delivery.status, func.count(Delivery.id)).group_by(
        Delivery.campaign_id, Delivery.status
    )
    if campaign_id is not None:
        query = query.where(Delivery.campaign_id == campaign_id)
    counts: dict[int, DeliveryCounts] = {}
    for cid, state, n in (await session.execute(query)).all():
        setattr(counts.setdefault(cid, DeliveryCounts()), str(state).lower(), n)
    return counts


def _campaign_out(campaign: Campaign, counts: DeliveryCounts | None) -> CampaignOut:
    return CampaignOut(
        id=campaign.id,
        name=campaign.name,
        is_active=campaign.is_active,
        post_id=campaign.post_id,
        match_any_post=campaign.match_any_post,
        trigger_comments=campaign.trigger_comments,
        trigger_story_replies=campaign.trigger_story_replies,
        trigger_messages=campaign.trigger_messages,
        keywords=list(campaign.keywords),
        match_mode=campaign.match_mode,
        dm_message=campaign.dm_message,
        link_url=campaign.link_url,
        link_button_label=campaign.link_button_label,
        follow_gate_enabled=campaign.follow_gate_enabled,
        follow_gate_message=campaign.follow_gate_message,
        follow_gate_button_label=campaign.follow_gate_button_label,
        public_reply_messages=list(campaign.public_reply_messages),
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        deliveries=counts or DeliveryCounts(),
    )


async def _campaign_response(sf: SessionFactory, campaign_id: int) -> CampaignOut:
    async with sf() as session:
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None or campaign.deleted_at is not None:
            raise _not_found("campaign")
        counts = await _counts(session, campaign_id)
    return _campaign_out(campaign, counts.get(campaign_id))


@router.get("/limits")
async def limits() -> LimitsOut:
    return LimitsOut(
        name_max=NAME_MAX_CHARS,
        keywords_max=MAX_KEYWORDS,
        keyword_max=KEYWORD_MAX_CHARS,
        message_max=MESSAGE_TEXT_MAX_CHARS,
        button_title_max=BUTTON_TITLE_MAX_CHARS,
        link_max=LINK_MAX_CHARS,
        public_replies_max=MAX_PUBLIC_REPLIES,
        public_reply_max=PUBLIC_REPLY_MAX_CHARS,
        gate_message_max=GATE_MESSAGE_MAX_CHARS,
    )


@router.post("/campaigns/match-test")
async def match_test(body: MatchTestIn) -> MatchTestOut:
    """Would this comment trigger these keywords? Runs the real matcher, so the form's
    answer can never differ from what the dispatcher does."""
    result = KeywordMatcher().match(body.text, body.keywords, body.match_mode)
    return MatchTestOut(matched=result.matched, keyword=result.keyword)


@router.get("/campaigns")
async def get_campaigns(session_factory: SessionFactoryDep) -> list[CampaignOut]:
    async with session_factory() as session:
        campaigns = await list_campaigns(session)
        counts = await _counts(session)
    return [_campaign_out(c, counts.get(c.id)) for c in campaigns]


@router.post("/campaigns", status_code=201)
async def post_campaign(body: CampaignInput, session_factory: SessionFactoryDep) -> CampaignOut:
    campaign = await create_campaign(session_factory, body)
    return await _campaign_response(session_factory, campaign.id)


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int, session_factory: SessionFactoryDep) -> CampaignOut:
    return await _campaign_response(session_factory, campaign_id)


@router.patch("/campaigns/{campaign_id}")
async def patch_campaign(
    campaign_id: int, body: CampaignPatch, session_factory: SessionFactoryDep
) -> CampaignOut:
    try:
        campaign = await update_campaign(
            session_factory, campaign_id, body.model_dump(exclude_unset=True)
        )
    except ValidationError as exc:
        raise invalid(exc) from None
    if campaign is None:
        raise _not_found("campaign")
    return await _campaign_response(session_factory, campaign_id)


@router.delete("/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: int, session_factory: SessionFactoryDep) -> Response:
    """Soft delete: deliveries keep their campaign for history."""
    if await set_campaign_state(session_factory, campaign_id, delete=True) is None:
        raise _not_found("campaign")
    return Response(status_code=204)


@router.post("/campaigns/{campaign_id}/duplicate", status_code=201)
async def duplicate(campaign_id: int, session_factory: SessionFactoryDep) -> CampaignOut:
    """The copy starts inactive."""
    copy = await duplicate_campaign(session_factory, campaign_id)
    if copy is None:
        raise _not_found("campaign")
    return await _campaign_response(session_factory, copy.id)


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: int, session_factory: SessionFactoryDep) -> CampaignOut:
    if await set_campaign_state(session_factory, campaign_id, active=False) is None:
        raise _not_found("campaign")
    return await _campaign_response(session_factory, campaign_id)


@router.post("/campaigns/{campaign_id}/resume")
async def resume_campaign(campaign_id: int, session_factory: SessionFactoryDep) -> CampaignOut:
    if await set_campaign_state(session_factory, campaign_id, active=True) is None:
        raise _not_found("campaign")
    return await _campaign_response(session_factory, campaign_id)


@router.get("/campaigns/{campaign_id}/stats")
async def campaign_stats(
    campaign_id: int,
    session_factory: SessionFactoryDep,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> CampaignStats:
    """Deliveries per UTC day. Clicks and CTR come with tracked links (phase 3)."""
    cutoff = utcnow() - timedelta(days=days)
    day = func.substr(Delivery.created_at, 1, 10, type_=String())
    async with session_factory() as session:
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None or campaign.deleted_at is not None:
            raise _not_found("campaign")
        rows = (
            await session.execute(
                select(day, Delivery.status, func.count(Delivery.id))
                .where(Delivery.campaign_id == campaign_id, Delivery.created_at >= cutoff)
                .group_by(day, Delivery.status)
                .order_by(day)
            )
        ).all()
        totals = (await _counts(session, campaign_id)).get(campaign_id, DeliveryCounts())
    per_day: dict[str, DayStats] = {}
    for d, state, n in rows:
        setattr(per_day.setdefault(d, DayStats(day=d)), str(state).lower(), n)
    return CampaignStats(campaign_id=campaign_id, days=list(per_day.values()), totals=totals)


# ---- deliveries --------------------------------------------------------------------------


def _delivery_out(delivery: Delivery, campaign_name: str) -> DeliveryOut:
    return DeliveryOut(
        id=delivery.id,
        created_at=delivery.created_at,
        campaign_id=delivery.campaign_id,
        campaign_name=campaign_name,
        trigger=delivery.trigger,
        origin=delivery.origin,
        media_id=delivery.media_id,
        actor_username=delivery.actor_username,
        text=delivery.text,
        matched_keyword=delivery.matched_keyword,
        status=delivery.status,
        attempts=delivery.attempts,
        dm_sent_at=delivery.dm_sent_at,
        error_message=delivery.error_message,
        public_reply_status=delivery.public_reply_status,
        public_reply_sent_at=delivery.public_reply_sent_at,
        public_reply_error=delivery.public_reply_error,
        gate_state=delivery.gate_state,
        gate_taps=delivery.gate_taps,
        followup_status=delivery.followup_status,
        followup_sent_at=delivery.followup_sent_at,
        followup_error=delivery.followup_error,
        anonymized=delivery.anonymized_at is not None,
    )


@router.get("/deliveries")
async def deliveries(
    session_factory: SessionFactoryDep,
    campaign_id: int | None = None,
    status: DeliveryStatus | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before: Annotated[int | None, Query(description="cursor: next_cursor of the last page")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DeliveryPage:
    query = (
        select(Delivery, Campaign.name)
        .join(Campaign, Campaign.id == Delivery.campaign_id)
        .order_by(Delivery.id.desc())
        .limit(limit + 1)
    )
    if campaign_id is not None:
        query = query.where(Delivery.campaign_id == campaign_id)
    if status is not None:
        query = query.where(Delivery.status == status)
    if (start := _aware(since, "since")) is not None:
        query = query.where(Delivery.created_at >= start)
    if (end := _aware(until, "until")) is not None:
        query = query.where(Delivery.created_at < end)
    if before is not None:
        query = query.where(Delivery.id < before)
    async with session_factory() as session:
        rows = (await session.execute(query)).all()
    items = [_delivery_out(d, name) for d, name in rows[:limit]]
    next_cursor = items[-1].id if len(rows) > limit else None
    return DeliveryPage(items=items, next_cursor=next_cursor)


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(
    delivery_id: int,
    session_factory: SessionFactoryDep,
    body: Annotated[RetryIn | None, Body()] = None,
) -> DeliveryOut:
    """Requeue the DM of a FAILED delivery. UNCONFIRMED needs the owner to have checked
    the Instagram inbox first: resending something that did arrive sends it twice."""
    confirmed = body is not None and body.confirm_not_delivered
    async with session_factory() as session, session.begin():
        delivery = await session.get(Delivery, delivery_id)
        if delivery is None:
            raise _not_found("delivery")
        if delivery.anonymized_at is not None:
            raise ApiError(409, "delivery is past retention and can no longer be sent")
        if delivery.status == DeliveryStatus.UNCONFIRMED and not confirmed:
            raise ApiError(
                422,
                "check the Instagram inbox, then send confirm_not_delivered: true",
            )
        if delivery.status not in (DeliveryStatus.FAILED, DeliveryStatus.UNCONFIRMED):
            raise ApiError(409, f"only FAILED or UNCONFIRMED can be retried, not {delivery.status}")
        if delivery.dm_sent_at is not None:
            raise ApiError(409, "this DM was already sent")
        now = utcnow()
        # The DM job is a private reply for a comment and a direct message for a story reply.
        job = (
            await session.execute(
                select(Job).where(Job.delivery_id == delivery.id, Job.type.in_(DM_JOB_TYPES))
            )
        ).scalar_one_or_none()
        if job is None:
            direct = delivery.trigger == Trigger.STORY_REPLY
            job = Job(
                type=JobType.DIRECT_MESSAGE if direct else JobType.PRIVATE_REPLY,
                delivery_id=delivery.id,
                dedupe_key=f"{'direct' if direct else 'private'}:{delivery.id}",
            )
            session.add(job)
        previous = delivery.status
        job.status = JobStatus.PENDING
        job.run_at = now
        job.attempts = 0
        job.locked_until = None
        job.send_started_at = None
        job.last_error = None
        delivery.status = DeliveryStatus.PENDING
        delivery.error_message = None
        record(
            session,
            OpsSource.WORKER,
            OpsLevel.INFO,
            f"delivery {delivery.id} requeued from {previous} by the owner",
        )
        campaign = await session.get(Campaign, delivery.campaign_id)
        out = _delivery_out(delivery, campaign.name if campaign else "")
    return out


# ---- operation ---------------------------------------------------------------------------


@router.get("/ops-events")
async def ops_events(
    session_factory: SessionFactoryDep,
    level: str | None = None,
    unresolved: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[OpsEventOut]:
    query = select(OpsEvent).order_by(OpsEvent.id.desc()).limit(limit)
    if level:
        query = query.where(OpsEvent.level == level.upper())
    if unresolved:
        query = query.where(OpsEvent.resolved_at.is_(None))
    async with session_factory() as session:
        rows = (await session.execute(query)).scalars().all()
    return [OpsEventOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/ops-events/{event_id}/resolve")
async def resolve_ops_event(event_id: int, session_factory: SessionFactoryDep) -> OpsEventOut:
    """Acknowledge an error so it stops counting in /status."""
    async with session_factory() as session, session.begin():
        event = await session.get(OpsEvent, event_id)
        if event is None:
            raise _not_found("ops event")
        if event.resolved_at is None:
            event.resolved_at = utcnow()
        out = OpsEventOut.model_validate(event, from_attributes=True)
    return out


@router.get("/webhook-events")
async def webhook_events(
    session_factory: SessionFactoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    with_text: bool = False,
) -> list[WebhookEventOut]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(WebhookEvent).order_by(WebhookEvent.id.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        WebhookEventOut(
            id=e.id,
            received_at=e.received_at,
            status=e.status,
            error=e.error,
            changes=summarize_webhook_payload(e.payload, with_text=with_text),
        )
        for e in rows
    ]


@router.post("/reconcile")
async def reconcile_now(
    request: Request, settings: SettingsDep, session_factory: SessionFactoryDep, meta: MetaDep
) -> ReconcileOut:
    """One sweep now. Shares a lock with the background loop, so sweeps never overlap."""
    async with session_factory() as session:
        account = await session.get(Account, 1)
    if account is None or account.status != AccountStatus.ACTIVE:
        raise ApiError(409, "no active account: store a valid token first")
    lock: asyncio.Lock = request.app.state.reconcile_lock
    async with lock:
        queued = await reconcile(session_factory, settings, meta, KeywordMatcher())
    if queued:
        wake: asyncio.Event | None = getattr(request.app.state, "worker_wake", None)
        if wake is not None:
            wake.set()
    return ReconcileOut(queued=queued)


# ---- dashboard login throttle ------------------------------------------------------------


@router.post("/login-throttle/check")
async def throttle_check(body: ThrottleKeyIn, session_factory: SessionFactoryDep) -> ThrottleOut:
    """Call before checking a password. retry_after > 0 means refuse without checking."""
    return ThrottleOut(retry_after=await login_throttle.check(session_factory, body.key))


@router.post("/login-throttle/failure")
async def throttle_failure(body: ThrottleKeyIn, session_factory: SessionFactoryDep) -> ThrottleOut:
    wait = await login_throttle.record_failure(session_factory, body.key)
    if wait > 0:
        # A typo is not news; a client that got blocked is. The throttle bounds how many of
        # these can be written, so a flood of guesses cannot fill the table.
        async with session_factory() as session, session.begin():
            record(
                session,
                OpsSource.SYSTEM,
                OpsLevel.WARNING,
                f"dashboard login blocked after repeated failures (retry in {wait} s)",
            )
    return ThrottleOut(retry_after=wait)


@router.post("/login-throttle/success", status_code=204)
async def throttle_success(body: ThrottleKeyIn, session_factory: SessionFactoryDep) -> Response:
    await login_throttle.record_success(session_factory, body.key)
    return Response(status_code=204)
