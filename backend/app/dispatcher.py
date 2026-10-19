"""Turns stored webhook events (and reconciler finds) into deliveries and jobs.

Idempotency lives here: a delivery insert that hits a unique constraint means this trigger was
already handled, by an earlier webhook delivery or by the reconciler, and nothing is queued.
"""

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import ChatContext, IncomingDM, handle_echo, handle_message, parse_messages
from app.db import utcnow
from app.flows import active_flows, load_chat_config
from app.gate import MAX_TAPS, parse_gate_payload
from app.matching import TriggerMatcher
from app.models import (
    Account,
    Campaign,
    Delivery,
    DeliveryStatus,
    GateState,
    Job,
    JobType,
    MatchMode,
    Origin,
    PostbackReceipt,
    Trigger,
    WebhookEvent,
    WebhookStatus,
)

logger = logging.getLogger(__name__)

# Public reply goes out a little after the DM, so "te mandei no direct" is true when read.
PUBLIC_REPLY_DELAY = timedelta(seconds=10)


@dataclass(frozen=True)
class IncomingComment:
    comment_id: str
    text: str
    from_id: str
    from_username: str | None
    media_id: str | None
    original_media_id: str | None
    owner_id: str


@dataclass(frozen=True)
class IncomingStoryReply:
    mid: str
    text: str
    sender_id: str
    story_id: str | None
    owner_id: str


@dataclass(frozen=True)
class IncomingPostback:
    """A tap on a follow-gate button."""

    mid: str
    sender_id: str
    delivery_id: int
    owner_id: str


class TapOutcome(StrEnum):
    QUEUED = "QUEUED"
    OWN_ACCOUNT = "OWN_ACCOUNT"
    DUPLICATE = "DUPLICATE"
    UNKNOWN_DELIVERY = "UNKNOWN_DELIVERY"
    NOT_GATED = "NOT_GATED"
    WRONG_USER = "WRONG_USER"
    ALREADY_RELEASED = "ALREADY_RELEASED"
    TOO_MANY = "TOO_MANY"


class Outcome(StrEnum):
    QUEUED = "QUEUED"
    OWN_COMMENT = "OWN_COMMENT"
    NO_MATCH = "NO_MATCH"
    DUPLICATE = "DUPLICATE"


def parse_comments(payload: dict[str, Any]) -> Iterator[IncomingComment]:
    """Comment changes from an Instagram webhook body. Anything else is ignored."""
    if payload.get("object") != "instagram":
        return
    for entry in payload.get("entry") or []:
        owner_id = str(entry.get("id") or "")
        for change in entry.get("changes") or []:
            if change.get("field") != "comments":
                continue
            value = change.get("value") or {}
            sender = value.get("from") or {}
            media = value.get("media") or {}
            comment_id = value.get("id")
            if not comment_id or not sender.get("id"):
                continue
            yield IncomingComment(
                comment_id=str(comment_id),
                text=str(value.get("text") or ""),
                from_id=str(sender["id"]),
                from_username=sender.get("username"),
                media_id=str(media["id"]) if media.get("id") else None,
                original_media_id=(
                    str(media["original_media_id"]) if media.get("original_media_id") else None
                ),
                owner_id=owner_id,
            )


def message_events(payload: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """(owner id, event) for every messages event. Meta documents two shapes for the same
    event, entry[].messaging[] and entry[].changes[] with field 'messages', so both are read."""
    if payload.get("object") != "instagram":
        return
    for entry in payload.get("entry") or []:
        owner_id = str(entry.get("id") or "")
        for event in entry.get("messaging") or []:
            yield owner_id, event
        for change in entry.get("changes") or []:
            if change.get("field") in ("messages", "messaging_postbacks"):
                yield owner_id, change.get("value") or {}


def parse_story_replies(payload: dict[str, Any]) -> Iterator[IncomingStoryReply]:
    """Replies to a story that carry text. Echoes, deleted and empty messages never trigger
    anything: without the echo filter, an automatic reply that contains its own keyword would
    answer itself forever."""
    for owner_id, event in message_events(payload):
        message = event.get("message") or {}
        story = (message.get("reply_to") or {}).get("story")
        sender_id = (event.get("sender") or {}).get("id")
        mid = message.get("mid")
        text = message.get("text")
        if not story or not mid or not sender_id:
            continue
        if message.get("is_echo") or message.get("is_deleted") or message.get("is_self"):
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        story_id = story.get("id") if isinstance(story, dict) else None
        yield IncomingStoryReply(
            mid=str(mid),
            text=text,
            sender_id=str(sender_id),
            story_id=str(story_id) if story_id else None,
            owner_id=owner_id,
        )


def parse_postbacks(payload: dict[str, Any]) -> Iterator[IncomingPostback]:
    """Taps on a follow-gate button. Buttons of other kinds (the diagnostics probe, anything
    we did not send) are ignored: only a payload in our exact format counts."""
    for owner_id, event in message_events(payload):
        postback = event.get("postback")
        if not isinstance(postback, dict):
            continue
        mid = postback.get("mid")
        sender_id = (event.get("sender") or {}).get("id")
        delivery_id = parse_gate_payload(postback.get("payload"))
        if not mid or not sender_id or delivery_id is None:
            continue
        yield IncomingPostback(
            mid=str(mid), sender_id=str(sender_id), delivery_id=delivery_id, owner_id=owner_id
        )


def _candidates(campaigns: Sequence[Campaign], comment: IncomingComment) -> list[Campaign]:
    """Campaigns that cover this post: post-specific ones first, then 'any post', oldest first."""
    media_ids = {m for m in (comment.media_id, comment.original_media_id) if m}
    campaigns = [c for c in campaigns if c.trigger_comments]
    specific = [c for c in campaigns if c.post_id and c.post_id in media_ids]
    any_post = [c for c in campaigns if c.match_any_post and c not in specific]
    return sorted(specific, key=lambda c: c.id) + sorted(any_post, key=lambda c: c.id)


async def active_campaigns(session: AsyncSession) -> Sequence[Campaign]:
    result = await session.execute(
        select(Campaign).where(Campaign.is_active.is_(True), Campaign.deleted_at.is_(None))
    )
    return result.scalars().all()


async def enqueue_comment(
    session: AsyncSession,
    comment: IncomingComment,
    *,
    origin: Origin,
    campaigns: Sequence[Campaign],
    matcher: TriggerMatcher,
    account_ig_id: str | None,
    now: datetime | None = None,
) -> Outcome:
    """Create the delivery and its jobs for a comment, at most once per comment."""
    now = now or utcnow()
    if comment.from_id in {comment.owner_id, account_ig_id}:
        return Outcome.OWN_COMMENT

    chosen: Campaign | None = None
    keyword: str | None = None
    for campaign in _candidates(campaigns, comment):
        result = matcher.match(comment.text, campaign.keywords, MatchMode(campaign.match_mode))
        if result.matched:
            chosen, keyword = campaign, result.keyword
            break
    if chosen is None:
        return Outcome.NO_MATCH

    public = chosen.public_reply_enabled and bool(chosen.public_reply_messages)
    inserted = await session.execute(
        sqlite_insert(Delivery)
        .values(
            campaign_id=chosen.id,
            trigger=Trigger.COMMENT,
            origin=origin,
            source_id=comment.comment_id,
            media_id=comment.media_id,
            actor_id=comment.from_id,
            actor_username=comment.from_username,
            text=comment.text,
            matched_keyword=keyword,
            status=DeliveryStatus.PENDING,
            attempts=0,
            public_reply_status=DeliveryStatus.PENDING if public else None,
            gate_state=GateState.WAITING if chosen.follow_gate_enabled else None,
            created_at=now,
            updated_at=now,
        )
        # Either unique constraint firing means this comment was already handled.
        .on_conflict_do_nothing()
        .returning(Delivery.id)
    )
    delivery_id = inserted.scalar_one_or_none()
    if delivery_id is None:
        return Outcome.DUPLICATE

    session.add(
        Job(
            type=JobType.PRIVATE_REPLY,
            delivery_id=delivery_id,
            dedupe_key=f"private:{delivery_id}",
            run_at=now,
        )
    )
    if public:
        session.add(
            Job(
                type=JobType.PUBLIC_REPLY,
                delivery_id=delivery_id,
                dedupe_key=f"public:{delivery_id}",
                run_at=now + PUBLIC_REPLY_DELAY,
            )
        )
    return Outcome.QUEUED


async def enqueue_story_reply(
    session: AsyncSession,
    reply: IncomingStoryReply,
    *,
    campaigns: Sequence[Campaign],
    matcher: TriggerMatcher,
    account_ig_id: str | None,
    now: datetime | None = None,
) -> Outcome:
    """Create the delivery and its DM job for a story reply, at most once per message."""
    now = now or utcnow()
    if reply.sender_id in {reply.owner_id, account_ig_id}:
        return Outcome.OWN_COMMENT

    chosen: Campaign | None = None
    keyword: str | None = None
    for campaign in sorted((c for c in campaigns if c.trigger_story_replies), key=lambda c: c.id):
        result = matcher.match(reply.text, campaign.keywords, MatchMode(campaign.match_mode))
        if result.matched:
            chosen, keyword = campaign, result.keyword
            break
    if chosen is None:
        return Outcome.NO_MATCH

    inserted = await session.execute(
        sqlite_insert(Delivery)
        .values(
            campaign_id=chosen.id,
            trigger=Trigger.STORY_REPLY,
            origin=Origin.WEBHOOK,
            source_id=reply.mid,
            media_id=reply.story_id,
            actor_id=reply.sender_id,
            actor_username=None,  # not in the webhook; the worker looks it up before sending
            text=reply.text,
            matched_keyword=keyword,
            status=DeliveryStatus.PENDING,
            attempts=0,
            public_reply_status=None,  # there is no comment to answer in public
            gate_state=GateState.WAITING if chosen.follow_gate_enabled else None,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing()
        .returning(Delivery.id)
    )
    delivery_id = inserted.scalar_one_or_none()
    if delivery_id is None:
        return Outcome.DUPLICATE
    session.add(
        Job(
            type=JobType.DIRECT_MESSAGE,
            delivery_id=delivery_id,
            dedupe_key=f"direct:{delivery_id}",
            run_at=now,
        )
    )
    return Outcome.QUEUED


async def enqueue_gate_tap(
    session: AsyncSession,
    tap: IncomingPostback,
    *,
    account_ig_id: str | None,
    now: datetime | None = None,
) -> TapOutcome:
    """Turn a tap into a follow check. Every tap is looked at once (the receipt), only a tap
    from the person the invite went to counts, and the number of taps is capped."""
    now = now or utcnow()
    if tap.sender_id in {tap.owner_id, account_ig_id}:
        return TapOutcome.OWN_ACCOUNT
    receipt = await session.execute(
        sqlite_insert(PostbackReceipt)
        .values(mid=tap.mid, created_at=now)
        .on_conflict_do_nothing()
        .returning(PostbackReceipt.mid)
    )
    if receipt.scalar_one_or_none() is None:
        return TapOutcome.DUPLICATE

    delivery = await session.get(Delivery, tap.delivery_id)
    if delivery is None or delivery.anonymized_at is not None:
        return TapOutcome.UNKNOWN_DELIVERY
    if delivery.gate_state is None:
        return TapOutcome.NOT_GATED
    # A story reply already tells us who the person is. For a comment, the first tap does.
    expected = delivery.gate_user_id or (
        delivery.actor_id if delivery.trigger == Trigger.STORY_REPLY else None
    )
    if expected is not None and expected != tap.sender_id:
        return TapOutcome.WRONG_USER
    if delivery.gate_state in (GateState.PASSED, GateState.RELEASED):
        return TapOutcome.ALREADY_RELEASED
    if delivery.gate_state == GateState.STOPPED:
        return TapOutcome.TOO_MANY

    delivery.gate_user_id = tap.sender_id
    delivery.gate_tapped_at = now
    delivery.gate_taps += 1
    if delivery.gate_taps > MAX_TAPS:
        delivery.gate_state = GateState.STOPPED
        return TapOutcome.TOO_MANY
    delivery.gate_state = GateState.CHECKING
    session.add(
        Job(
            type=JobType.FOLLOW_CHECK,
            delivery_id=delivery.id,
            dedupe_key=f"gate:{delivery.id}:{tap.mid}:1",
            run_at=now,
            payload={"check": 1},
        )
    )
    return TapOutcome.QUEUED


async def dispatch_pending(
    session_factory: async_sessionmaker[AsyncSession],
    matcher: TriggerMatcher,
    *,
    batch_size: int = 50,
    now: datetime | None = None,
    ai_available: bool = False,
) -> int:
    """Process pending webhook events, one transaction per event. Returns jobs queued
    (campaign jobs and chat outbox rows)."""
    queued = 0
    async with session_factory() as session:
        event_ids = (
            (
                await session.execute(
                    select(WebhookEvent.id)
                    .where(WebhookEvent.status == WebhookStatus.PENDING)
                    .order_by(WebhookEvent.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
    for event_id in event_ids:
        try:
            async with session_factory() as session, session.begin():
                event = await session.get(WebhookEvent, event_id)
                if event is None or event.status != WebhookStatus.PENDING:
                    continue
                account = await session.get(Account, 1)
                campaigns = await active_campaigns(session)
                for comment in parse_comments(event.payload):
                    outcome = await enqueue_comment(
                        session,
                        comment,
                        origin=Origin.WEBHOOK,
                        campaigns=campaigns,
                        matcher=matcher,
                        account_ig_id=account.ig_user_id if account else None,
                        now=now,
                    )
                    queued += outcome == Outcome.QUEUED
                queued += await _dispatch_messages(
                    session,
                    event.payload,
                    campaigns=campaigns,
                    matcher=matcher,
                    account_ig_id=account.ig_user_id if account else None,
                    ai_available=ai_available,
                    now=now or utcnow(),
                )
                for tap in parse_postbacks(event.payload):
                    tap_outcome = await enqueue_gate_tap(
                        session,
                        tap,
                        account_ig_id=account.ig_user_id if account else None,
                        now=now,
                    )
                    queued += tap_outcome == TapOutcome.QUEUED
                event.status = WebhookStatus.PROCESSED
                event.processed_at = utcnow()
        except Exception as exc:
            # Never store the exception text: it may quote the payload (comment text).
            logger.exception("dispatcher: event %s failed", event_id)
            async with session_factory() as session, session.begin():
                event = await session.get(WebhookEvent, event_id)
                if event is not None:
                    event.status = WebhookStatus.FAILED
                    event.error = type(exc).__name__
                    event.processed_at = utcnow()
    return queued


async def _dispatch_messages(
    session: AsyncSession,
    payload: dict[str, Any],
    *,
    campaigns: Sequence[Campaign],
    matcher: TriggerMatcher,
    account_ig_id: str | None,
    ai_available: bool,
    now: datetime,
) -> int:
    """Direct messages, story replies and echoes, through the chat routing (app.chat)."""
    messages = list(parse_messages(message_events(payload)))
    if not messages:
        return 0
    ctx = ChatContext(
        campaigns=campaigns,
        flows=await active_flows(session),
        matcher=matcher,
        config=await load_chat_config(session),
        account_ig_id=account_ig_id,
        ai_available=ai_available,
        now=now,
    )
    queued = 0
    for dm in messages:
        if dm.is_echo:
            await handle_echo(session, dm, ctx)
            continue

        async def story_reply(message: IncomingDM) -> bool:
            if message.text is None:
                return False
            outcome = await enqueue_story_reply(
                session,
                IncomingStoryReply(
                    mid=message.mid,
                    text=message.text,
                    sender_id=message.sender_id,
                    story_id=message.story_id,
                    owner_id=message.owner_id,
                ),
                campaigns=campaigns,
                matcher=matcher,
                account_ig_id=account_ig_id,
                now=now,
            )
            return outcome == Outcome.QUEUED

        queued += await handle_message(session, dm, ctx, story_reply_handler=story_reply)
    return queued
