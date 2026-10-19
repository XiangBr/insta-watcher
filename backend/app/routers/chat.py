"""Chat API: conversations, flows, settings and the AI test. Reads the chat tables; sending is
the chat worker's job (app.chat_worker)."""

import asyncio
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import chat_ai
from app.api_models import (
    AiTestIn,
    AiTestOut,
    ChatConfigOut,
    ChatMessageOut,
    ChatSummary,
    ChatUsage,
    ContactDetail,
    ContactOut,
    ContactPatch,
    ContactsPage,
    FlowOut,
    OutboxOut,
    ReplyIn,
)
from app.chat import enqueue_outbox, start_human_mode
from app.db import utcnow
from app.errors import ApiError
from app.flows import (
    ChatConfig,
    FlowInput,
    FlowStep,
    active_flows,
    apply_flow,
    clean_tags,
    load_chat_config,
    save_chat_config,
    soft_delete_flow,
)
from app.meta.limits import MESSAGING_WINDOW
from app.models import (
    Account,
    ChatMessage,
    ChatOutbox,
    ChatSource,
    Contact,
    Flow,
    MatchMode,
    OutboxKind,
    OutboxStatus,
)
from app.openrouter import OpenRouterClient, OpenRouterError
from app.routers.admin import require_admin
from app.routers.public import SessionFactoryDep, SettingsDep

router = APIRouter(prefix="/api/v1/chat", dependencies=[Depends(require_admin)])

PREVIEW_CHARS = 120
MESSAGES_SHOWN = 200


def _wake(request: Request) -> None:
    event: asyncio.Event | None = getattr(request.app.state, "chat_wake", None)
    if event is not None:
        event.set()


def _contact_out(c: Contact, last: ChatMessage | None) -> ContactOut:
    now = utcnow()
    closes = (
        c.last_inbound_at + MESSAGING_WINDOW
        if c.last_inbound_at and c.last_inbound_at + MESSAGING_WINDOW > now
        else None
    )
    return ContactOut(
        id=c.id,
        username=c.username,
        tags=list(c.tags or []),
        first_seen_at=c.first_seen_at,
        last_inbound_at=c.last_inbound_at,
        last_outbound_at=c.last_outbound_at,
        window_closes_at=closes,
        human_until=c.human_until if c.human_until and c.human_until > now else None,
        human_reason=c.human_reason if c.human_until and c.human_until > now else None,
        messages_in=c.messages_in,
        messages_out=c.messages_out,
        last_text=(last.text[:PREVIEW_CHARS] if last and last.text else None),
        last_source=last.source if last else None,
    )


def _outbox_out(row: ChatOutbox) -> OutboxOut:
    text = row.payload.get("text") if isinstance(row.payload, dict) else None
    preview = str(text)[:PREVIEW_CHARS] if text else None
    if preview is None and row.kind == OutboxKind.STEP:
        preview = f"passo {row.payload.get('step_id')}"
    return OutboxOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        run_at=row.run_at,
        preview=preview,
        error=row.error,
        created_at=row.created_at,
    )


async def _summary(session: AsyncSession) -> ChatSummary:
    now = utcnow()
    day = now - timedelta(days=1)

    async def count(query: object) -> int:
        return int(await session.scalar(query) or 0)  # type: ignore[call-overload]

    usage = await _usage(session)
    return ChatSummary(
        contacts=await count(select(func.count()).select_from(Contact)),
        open_windows=await count(
            select(func.count()).select_from(Contact).where(Contact.last_inbound_at > day)
        ),
        in_human_mode=await count(
            select(func.count()).select_from(Contact).where(Contact.human_until > now)
        ),
        ai_replies_24h=usage.ai_replies_24h,
        ai_cost_24h_usd=usage.ai_cost_24h_usd,
        scheduled=await count(
            select(func.count())
            .select_from(ChatOutbox)
            .where(ChatOutbox.status == OutboxStatus.PENDING)
        ),
        failed_24h=await count(
            select(func.count())
            .select_from(ChatOutbox)
            .where(
                ChatOutbox.status.in_((OutboxStatus.FAILED, OutboxStatus.UNCONFIRMED)),
                ChatOutbox.updated_at >= day,
            )
        ),
    )


async def _last_messages(session: AsyncSession, ids: list[int]) -> dict[int, ChatMessage]:
    if not ids:
        return {}
    newest = (
        select(ChatMessage.contact_id, func.max(ChatMessage.id).label("mid"))
        .where(ChatMessage.contact_id.in_(ids))
        .group_by(ChatMessage.contact_id)
        .subquery()
    )
    rows = await session.scalars(select(ChatMessage).join(newest, ChatMessage.id == newest.c.mid))
    return {m.contact_id: m for m in rows}


# ---- conversations -----------------------------------------------------------------------


@router.get("/contacts")
async def contacts(
    session_factory: SessionFactoryDep,
    search: Annotated[str | None, Query(max_length=64)] = None,
    tag: Annotated[str | None, Query(max_length=32)] = None,
    view: Annotated[str, Query(pattern="^(all|open|human)$")] = "all",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ContactsPage:
    """People who wrote to the account, the most recent conversation first."""
    now = utcnow()
    query = select(Contact).where(Contact.anonymized_at.is_(None))
    if search:
        query = query.where(Contact.username.contains(search.strip().lstrip("@").lower()))
    if view == "open":
        query = query.where(Contact.last_inbound_at > now - MESSAGING_WINDOW)
    elif view == "human":
        query = query.where(Contact.human_until > now)
    async with session_factory() as session:
        rows = list(
            await session.scalars(
                query.order_by(func.coalesce(Contact.last_inbound_at, Contact.first_seen_at).desc())
            )
        )
        if tag:
            rows = [c for c in rows if tag in (c.tags or [])]
        page = rows[offset : offset + limit]
        last = await _last_messages(session, [c.id for c in page])
        summary = await _summary(session)
        all_tags = sorted(
            {t for tags in await session.scalars(select(Contact.tags)) for t in (tags or [])}
        )
    return ContactsPage(
        summary=summary,
        contacts=[_contact_out(c, last.get(c.id)) for c in page],
        total=len(rows),
        tags=all_tags,
    )


async def _detail(session: AsyncSession, contact: Contact) -> ContactDetail:
    messages = list(
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.contact_id == contact.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(MESSAGES_SHOWN)
        )
    )[::-1]
    scheduled = list(
        await session.scalars(
            select(ChatOutbox)
            .where(
                ChatOutbox.contact_id == contact.id,
                ChatOutbox.status.in_((OutboxStatus.PENDING, OutboxStatus.RUNNING)),
            )
            .order_by(ChatOutbox.run_at)
        )
    )
    problems = list(
        await session.scalars(
            select(ChatOutbox)
            .where(
                ChatOutbox.contact_id == contact.id,
                ChatOutbox.status.in_((OutboxStatus.FAILED, OutboxStatus.UNCONFIRMED)),
            )
            .order_by(ChatOutbox.updated_at.desc())
            .limit(10)
        )
    )
    return ContactDetail(
        contact=_contact_out(contact, messages[-1] if messages else None),
        messages=[
            ChatMessageOut(
                id=m.id,
                direction=m.direction,
                source=m.source,
                text=m.text,
                payload=m.payload,
                flow_id=m.flow_id,
                step_id=m.step_id,
                cost_usd=m.cost_usd,
                created_at=m.created_at,
            )
            for m in messages
        ],
        scheduled=[_outbox_out(o) for o in scheduled],
        problems=[_outbox_out(o) for o in problems],
    )


async def _get_contact(session: AsyncSession, contact_id: int) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.anonymized_at is not None:
        raise ApiError(404, "contact not found")
    return contact


@router.get("/contacts/{contact_id}")
async def contact(contact_id: int, session_factory: SessionFactoryDep) -> ContactDetail:
    async with session_factory() as session:
        return await _detail(session, await _get_contact(session, contact_id))


@router.patch("/contacts/{contact_id}")
async def patch_contact(
    contact_id: int, body: ContactPatch, session_factory: SessionFactoryDep
) -> ContactDetail:
    """Edit the tags, or pause (human: true) and resume (human: false) the automation."""
    async with session_factory() as session, session.begin():
        found = await _get_contact(session, contact_id)
        if body.tags is not None:
            try:
                found.tags = clean_tags(body.tags)
            except ValueError as exc:
                raise ApiError(422, str(exc)) from None
        if body.human is True:
            config = await load_chat_config(session)
            await start_human_mode(session, found, config.handoff_hours, "panel", utcnow())
        elif body.human is False:
            found.human_until, found.human_reason = None, None
    async with session_factory() as session:
        return await _detail(session, await _get_contact(session, contact_id))


@router.post("/contacts/{contact_id}/messages", status_code=202)
async def reply(
    contact_id: int, body: ReplyIn, request: Request, session_factory: SessionFactoryDep
) -> ContactDetail:
    """Send a message typed in the panel. It pauses the automation for this person, like a
    reply typed in the Instagram app. Only inside the 24 h window."""
    now = utcnow()
    async with session_factory() as session, session.begin():
        found = await _get_contact(session, contact_id)
        if found.last_inbound_at is None or now - found.last_inbound_at >= MESSAGING_WINDOW:
            raise ApiError(
                409,
                "the 24 h window is closed: Meta only allows a reply after the person writes again",
            )
        config = await load_chat_config(session)
        await start_human_mode(session, found, config.handoff_hours, "panel_reply", now)
        await enqueue_outbox(
            session,
            found,
            OutboxKind.TEXT,
            {"text": body.text.strip(), "source": ChatSource.OWNER},
            f"panel:{found.id}:{now.timestamp()}",
            now,
        )
    _wake(request)
    async with session_factory() as session:
        return await _detail(session, await _get_contact(session, contact_id))


@router.delete("/outbox/{outbox_id}", status_code=204)
async def cancel_scheduled(outbox_id: int, session_factory: SessionFactoryDep) -> Response:
    """Cancel one message that has not gone out yet."""
    async with session_factory() as session, session.begin():
        row = await session.get(ChatOutbox, outbox_id)
        if row is None:
            raise ApiError(404, "message not found")
        if row.status != OutboxStatus.PENDING:
            raise ApiError(409, "only a message still waiting can be cancelled")
        row.status, row.error = OutboxStatus.CANCELLED, "cancelled in the panel"
    return Response(status_code=204)


# ---- flows -------------------------------------------------------------------------------


async def _flow_out(session: AsyncSession, flow: Flow) -> FlowOut:
    stats = await session.execute(
        select(func.count(), func.count(distinct(ChatMessage.contact_id))).where(
            ChatMessage.flow_id == flow.id,
            ChatMessage.source.in_((ChatSource.FLOW, ChatSource.FOLLOWUP)),
        )
    )
    sent, people = stats.one()
    return FlowOut(
        id=flow.id,
        name=flow.name,
        is_active=flow.is_active,
        keywords=list(flow.keywords),
        match_mode=MatchMode(flow.match_mode),
        steps=[FlowStep.model_validate(s) for s in flow.steps],
        created_at=flow.created_at,
        updated_at=flow.updated_at,
        sent=int(sent or 0),
        people=int(people or 0),
    )


async def _get_flow(session: AsyncSession, flow_id: int) -> Flow:
    flow = await session.get(Flow, flow_id)
    if flow is None or flow.deleted_at is not None:
        raise ApiError(404, "flow not found")
    return flow


@router.get("/flows")
async def flows(session_factory: SessionFactoryDep) -> list[FlowOut]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(Flow).where(Flow.deleted_at.is_(None)).order_by(Flow.id)
        )
        return [await _flow_out(session, f) for f in rows]


async def _check_keywords(session: AsyncSession, data: FlowInput, own_id: int | None) -> None:
    """Two active flows with the same keyword would make the older one win silently."""
    if not data.is_active or not data.keywords:
        return
    mine = {k.lower() for k in data.keywords}
    for other in await active_flows(session):
        if other.id != own_id and mine & {k.lower() for k in other.keywords}:
            raise ApiError(409, f"keyword already used by the flow '{other.name}'")


@router.post("/flows", status_code=201)
async def create_flow(body: FlowInput, session_factory: SessionFactoryDep) -> FlowOut:
    async with session_factory() as session, session.begin():
        await _check_keywords(session, body, None)
        flow = Flow()
        apply_flow(flow, body)
        session.add(flow)
        await session.flush()
        return await _flow_out(session, flow)


@router.get("/flows/{flow_id}")
async def get_flow(flow_id: int, session_factory: SessionFactoryDep) -> FlowOut:
    async with session_factory() as session:
        return await _flow_out(session, await _get_flow(session, flow_id))


@router.put("/flows/{flow_id}")
async def update_flow(flow_id: int, body: FlowInput, session_factory: SessionFactoryDep) -> FlowOut:
    async with session_factory() as session, session.begin():
        flow = await _get_flow(session, flow_id)
        await _check_keywords(session, body, flow.id)
        apply_flow(flow, body)
        flow.updated_at = utcnow()
        await session.flush()
        return await _flow_out(session, flow)


@router.delete("/flows/{flow_id}", status_code=204)
async def delete_flow(flow_id: int, session_factory: SessionFactoryDep) -> Response:
    """Soft delete: the messages it sent keep pointing at it. Waiting steps are cancelled."""
    async with session_factory() as session, session.begin():
        flow = await _get_flow(session, flow_id)
        await soft_delete_flow(session, flow)
        config = await load_chat_config(session)
        if config.welcome_flow_id == flow.id:
            await save_chat_config(session, config.model_copy(update={"welcome_flow_id": None}))
    return Response(status_code=204)


# ---- settings and the AI -----------------------------------------------------------------


async def _usage(session: AsyncSession) -> ChatUsage:
    day = utcnow() - timedelta(days=1)
    rows = (
        await session.execute(
            select(
                ChatMessage.source, func.count(), func.coalesce(func.sum(ChatMessage.cost_usd), 0.0)
            )
            .where(
                ChatMessage.created_at >= day,
                ChatMessage.source.in_((ChatSource.AI, ChatSource.HANDOFF)),
            )
            .group_by(ChatMessage.source)
        )
    ).all()
    counts = {source: (int(n), float(cost or 0)) for source, n, cost in rows}
    ai_n, ai_cost = counts.get(ChatSource.AI, (0, 0.0))
    handoff_n, handoff_cost = counts.get(ChatSource.HANDOFF, (0, 0.0))
    return ChatUsage(
        ai_replies_24h=ai_n,
        ai_cost_24h_usd=round(ai_cost + handoff_cost, 4),
        handoffs_24h=handoff_n,
    )


@router.get("/config")
async def get_config(settings: SettingsDep, session_factory: SessionFactoryDep) -> ChatConfigOut:
    async with session_factory() as session:
        return ChatConfigOut(
            config=await load_chat_config(session),
            ai_available=settings.openrouter_api_key is not None,
            default_model=settings.chat_ai_model,
            usage=await _usage(session),
        )


@router.put("/config")
async def put_config(
    body: ChatConfig, settings: SettingsDep, session_factory: SessionFactoryDep
) -> ChatConfigOut:
    async with session_factory() as session, session.begin():
        if body.welcome_flow_id is not None:
            await _get_flow(session, body.welcome_flow_id)
        if body.ai_enabled and settings.openrouter_api_key is None:
            raise ApiError(409, "OPENROUTER_API_KEY is not set: the AI cannot be switched on")
        await save_chat_config(session, body)
        if not body.ai_enabled:
            # Switching the AI off also drops the answers it was about to write.
            pending = await session.scalars(
                select(ChatOutbox).where(
                    ChatOutbox.kind == OutboxKind.AI_REPLY,
                    ChatOutbox.status == OutboxStatus.PENDING,
                )
            )
            for row in pending:
                row.status, row.error = OutboxStatus.CANCELLED, "AI switched off"
    async with session_factory() as session:
        return ChatConfigOut(
            config=await load_chat_config(session),
            ai_available=settings.openrouter_api_key is not None,
            default_model=settings.chat_ai_model,
            usage=await _usage(session),
        )


@router.post("/ai/test")
async def test_ai(
    body: AiTestIn, request: Request, settings: SettingsDep, session_factory: SessionFactoryDep
) -> AiTestOut:
    """What the AI would answer to this message, with the saved (or the given) settings.
    Nothing is sent and nothing is stored. Billed by OpenRouter like a real answer."""
    client: OpenRouterClient | None = getattr(request.app.state, "openrouter", None)
    if client is None:
        raise ApiError(409, "OPENROUTER_API_KEY is not set")
    async with session_factory() as session:
        config = body.config or await load_chat_config(session)
        flows = await active_flows(session)
        account = await session.get(Account, 1)
    probe = ChatMessage(direction="IN", source=ChatSource.USER, text=body.message)
    messages = chat_ai.build_messages(config, account.username if account else "", flows, [probe])
    model = config.ai_model or settings.chat_ai_model
    try:
        result = await chat_ai.generate(client, model, messages)
    except OpenRouterError as exc:
        raise ApiError(502, str(exc)) from None
    return AiTestOut(
        reply=result.text,
        handoff=result.handoff,
        sent_text=config.handoff_message if result.handoff else result.text,
        model=model,
        cost_usd=result.cost_usd,
    )
