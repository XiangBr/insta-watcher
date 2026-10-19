"""Direct messages: who wrote, what they wrote, and what answers them.

Runs inside the dispatcher's transaction for each webhook event, so it only writes rows: the
chat worker (app.chat_worker) sends. For a message from a person, the first match wins:

1. the person is in human mode (handoff or a manual reply): nothing automatic answers;
2. a tap on a quick reply of one of our flows: that option's action;
3. a story reply that matches a story campaign (the existing campaign path);
4. a direct message that matches a direct-message campaign;
5. a message that matches a flow's keyword: the flow's first step;
6. the person's first message ever, with a welcome flow set: that flow;
7. the AI, when it is on: one reply, a few seconds later so a burst of messages gets one answer.

Echoes are our own messages coming back. One we did not send (typed in the Instagram app)
pauses the automation for that person, when pause_on_manual_reply is on.
"""

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import (
    ChatConfig,
    FlowStep,
    clean_tags,
    find_step,
    first_step,
    parse_option_payload,
)
from app.matching import TriggerMatcher
from app.models import (
    Campaign,
    ChatDirection,
    ChatMessage,
    ChatOutbox,
    ChatSource,
    Contact,
    Delivery,
    DeliveryStatus,
    Flow,
    GateState,
    Job,
    JobType,
    MatchMode,
    Origin,
    OutboxKind,
    OutboxStatus,
    Trigger,
)

logger = logging.getLogger(__name__)

# Messages that arrive this close together get one AI answer.
AI_DEBOUNCE = timedelta(seconds=8)
# An echo this soon after one of our sends is taken as ours, even before Meta's message id is
# stored (the echo can arrive before the send call returns).
OWN_SEND_GRACE = timedelta(minutes=2)
TEXT_MAX_STORED = 4000


@dataclass(frozen=True)
class IncomingDM:
    owner_id: str
    mid: str
    sender_id: str
    recipient_id: str
    text: str | None
    quick_reply_payload: str | None
    is_echo: bool
    story_id: str | None
    is_story_reply: bool


def parse_messages(events: Iterator[tuple[str, dict[str, Any]]]) -> Iterator[IncomingDM]:
    """Messages (not postbacks, reactions or reads). Deleted messages are skipped."""
    for owner_id, event in events:
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        mid = message.get("mid")
        sender = (event.get("sender") or {}).get("id")
        recipient = (event.get("recipient") or {}).get("id")
        if not mid or not sender or message.get("is_deleted"):
            continue
        text = message.get("text")
        quick = message.get("quick_reply")
        story = (message.get("reply_to") or {}).get("story")
        yield IncomingDM(
            owner_id=owner_id,
            mid=str(mid),
            sender_id=str(sender),
            recipient_id=str(recipient or ""),
            text=text if isinstance(text, str) and text.strip() else None,
            quick_reply_payload=str(quick.get("payload"))
            if isinstance(quick, dict) and quick.get("payload")
            else None,
            is_echo=bool(message.get("is_echo")),
            story_id=str(story["id"]) if isinstance(story, dict) and story.get("id") else None,
            is_story_reply=bool(story),
        )


@dataclass
class ChatContext:
    campaigns: Sequence[Campaign]
    flows: Sequence[Flow]
    matcher: TriggerMatcher
    config: ChatConfig
    account_ig_id: str | None
    ai_available: bool
    now: datetime


async def get_or_create_contact(
    session: AsyncSession, ig_user_id: str, now: datetime
) -> tuple[Contact, bool]:
    contact = await session.scalar(select(Contact).where(Contact.ig_user_id == ig_user_id))
    if contact is not None:
        return contact, False
    contact = Contact(
        ig_user_id=ig_user_id, first_seen_at=now, tags=[], messages_in=0, messages_out=0
    )
    session.add(contact)
    await session.flush()
    return contact, True


async def record_message(
    session: AsyncSession,
    contact: Contact,
    *,
    direction: ChatDirection,
    source: ChatSource,
    mid: str | None,
    text: str | None,
    now: datetime,
    payload: str | None = None,
    flow_id: int | None = None,
    step_id: str | None = None,
    cost_usd: float | None = None,
) -> bool:
    """Insert once per Meta message id. False if this message was already recorded."""
    result = await session.execute(
        sqlite_insert(ChatMessage)
        .values(
            contact_id=contact.id,
            direction=direction,
            source=source,
            mid=mid,
            text=text[:TEXT_MAX_STORED] if text else None,
            payload=payload[:256] if payload else None,
            flow_id=flow_id,
            step_id=step_id,
            cost_usd=cost_usd,
            created_at=now,
        )
        .on_conflict_do_nothing()
        .returning(ChatMessage.id)
    )
    return result.scalar_one_or_none() is not None


def in_human_mode(contact: Contact, now: datetime) -> bool:
    return contact.human_until is not None and contact.human_until > now


async def cancel_pending(session: AsyncSession, contact_id: int, reason: str) -> int:
    """Cancel everything automatic still waiting for this person."""
    result = await session.execute(
        update(ChatOutbox)
        .where(ChatOutbox.contact_id == contact_id, ChatOutbox.status == OutboxStatus.PENDING)
        .values(status=OutboxStatus.CANCELLED, error=reason)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def start_human_mode(
    session: AsyncSession, contact: Contact, hours: int, reason: str, now: datetime
) -> None:
    contact.human_until = now + timedelta(hours=hours)
    contact.human_reason = reason
    await cancel_pending(session, contact.id, f"human mode: {reason}")


def add_tags(contact: Contact, tags: list[str]) -> None:
    if tags:
        contact.tags = clean_tags([*(contact.tags or []), *tags])[:20]


async def enqueue_outbox(
    session: AsyncSession,
    contact: Contact,
    kind: OutboxKind,
    payload: dict[str, Any],
    dedupe_key: str,
    run_at: datetime,
    *,
    now: datetime | None = None,
) -> bool:
    """Queue one message. created_at is when it was decided (a follow-up compares it with the
    person's last message), run_at is when it goes out."""
    created = now or run_at
    result = await session.execute(
        sqlite_insert(ChatOutbox)
        .values(
            contact_id=contact.id,
            kind=kind,
            payload=payload,
            dedupe_key=dedupe_key[:128],
            status=OutboxStatus.PENDING,
            run_at=run_at,
            attempts=0,
            created_at=created,
            updated_at=created,
        )
        .on_conflict_do_nothing()
        .returning(ChatOutbox.id)
    )
    return result.scalar_one_or_none() is not None


async def enqueue_step(
    session: AsyncSession,
    contact: Contact,
    flow: Flow,
    step: FlowStep,
    trigger_mid: str,
    now: datetime,
) -> bool:
    add_tags(contact, step.tags)
    return await enqueue_outbox(
        session,
        contact,
        OutboxKind.STEP,
        {"flow_id": flow.id, "step_id": step.id},
        f"step:{trigger_mid}:{flow.id}:{step.id}",
        now,
    )


async def enqueue_direct_campaign(session: AsyncSession, dm: IncomingDM, ctx: ChatContext) -> bool:
    """A plain direct message that matches a direct-message campaign. Same delivery and job
    as a story reply, so the follow gate and the link button work the same way."""
    if dm.text is None:
        return False
    chosen: Campaign | None = None
    keyword: str | None = None
    for campaign in sorted((c for c in ctx.campaigns if c.trigger_messages), key=lambda c: c.id):
        result = ctx.matcher.match(dm.text, campaign.keywords, MatchMode(campaign.match_mode))
        if result.matched:
            chosen, keyword = campaign, result.keyword
            break
    if chosen is None:
        return False
    inserted = await session.execute(
        sqlite_insert(Delivery)
        .values(
            campaign_id=chosen.id,
            trigger=Trigger.MESSAGE,
            origin=Origin.WEBHOOK,
            source_id=dm.mid,
            media_id=None,
            actor_id=dm.sender_id,
            actor_username=None,
            text=dm.text,
            matched_keyword=keyword,
            status=DeliveryStatus.PENDING,
            attempts=0,
            public_reply_status=None,
            gate_state=GateState.WAITING if chosen.follow_gate_enabled else None,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .on_conflict_do_nothing()
        .returning(Delivery.id)
    )
    delivery_id = inserted.scalar_one_or_none()
    if delivery_id is None:
        return False
    session.add(
        Job(
            type=JobType.DIRECT_MESSAGE,
            delivery_id=delivery_id,
            dedupe_key=f"direct:{delivery_id}",
            run_at=ctx.now,
        )
    )
    return True


def match_flow(text: str | None, ctx: ChatContext) -> Flow | None:
    if not text:
        return None
    for flow in ctx.flows:
        if not flow.keywords:
            continue
        if ctx.matcher.match(text, flow.keywords, MatchMode(flow.match_mode)).matched:
            return flow
    return None


async def handle_option(
    session: AsyncSession, contact: Contact, dm: IncomingDM, ctx: ChatContext
) -> bool:
    parsed = parse_option_payload(dm.quick_reply_payload)
    if parsed is None:
        return False
    flow_id, step_id, index = parsed
    flow = next((f for f in ctx.flows if f.id == flow_id), None)
    step = find_step(flow, step_id) if flow is not None else None
    if flow is None or step is None or index >= len(step.options):
        return False  # an old button of a flow that changed or was switched off
    option = step.options[index]
    add_tags(contact, option.tags)
    if option.action == "step":
        target = find_step(flow, option.step or "")
        return target is not None and await enqueue_step(
            session, contact, flow, target, dm.mid, ctx.now
        )
    if option.action == "link":
        return await enqueue_outbox(
            session,
            contact,
            OutboxKind.LINK,
            {
                "flow_id": flow.id,
                "step_id": step.id,
                "url": option.url,
                "text": option.link_text,
                "label": option.link_label,
            },
            f"link:{dm.mid}",
            ctx.now,
        )
    # handoff: say so, then hold every automatic answer for this person.
    queued = await enqueue_outbox(
        session,
        contact,
        OutboxKind.TEXT,
        {"text": ctx.config.handoff_message, "source": ChatSource.HANDOFF},
        f"handoff:{dm.mid}",
        ctx.now,
    )
    contact.human_until = ctx.now + timedelta(hours=ctx.config.handoff_hours)
    contact.human_reason = "handoff_option"
    return queued


async def enqueue_ai(
    session: AsyncSession, contact: Contact, dm: IncomingDM, now: datetime
) -> bool:
    """One pending AI answer per person: a new message only pushes it a little later."""
    pending = await session.scalar(
        select(ChatOutbox).where(
            ChatOutbox.contact_id == contact.id,
            ChatOutbox.kind == OutboxKind.AI_REPLY,
            ChatOutbox.status == OutboxStatus.PENDING,
        )
    )
    if pending is not None:
        pending.run_at = now + AI_DEBOUNCE
        return False
    return await enqueue_outbox(
        session, contact, OutboxKind.AI_REPLY, {}, f"ai:{dm.mid}", now + AI_DEBOUNCE, now=now
    )


async def _is_our_echo(session: AsyncSession, contact: Contact, mid: str, now: datetime) -> bool:
    since = now - OWN_SEND_GRACE
    outbox = await session.scalar(
        select(ChatOutbox.id)
        .where(
            ChatOutbox.contact_id == contact.id,
            or_(
                ChatOutbox.message_id == mid,
                ChatOutbox.status == OutboxStatus.RUNNING,
                ChatOutbox.sent_at >= since,
            ),
        )
        .limit(1)
    )
    if outbox is not None:
        return True
    delivery = await session.scalar(
        select(Delivery.id)
        .where(
            or_(
                Delivery.dm_message_id == mid,
                Delivery.followup_message_id == mid,
                (Delivery.actor_id == contact.ig_user_id)
                & or_(Delivery.dm_sent_at >= since, Delivery.followup_sent_at >= since),
            )
        )
        .limit(1)
    )
    if delivery is not None:
        return True
    # A campaign DM still being sent: its job is running for this person.
    running = await session.scalar(
        select(Job.id)
        .join(Delivery, Delivery.id == Job.delivery_id)
        .where(Delivery.actor_id == contact.ig_user_id, Job.send_started_at >= since)
        .limit(1)
    )
    return running is not None


async def handle_echo(session: AsyncSession, dm: IncomingDM, ctx: ChatContext) -> None:
    if not dm.recipient_id:
        return
    contact, _ = await get_or_create_contact(session, dm.recipient_id, ctx.now)
    exists = await session.scalar(select(ChatMessage.id).where(ChatMessage.mid == dm.mid))
    if exists is not None:
        return  # the chat worker already recorded this send
    ours = await _is_our_echo(session, contact, dm.mid, ctx.now)
    source = ChatSource.CAMPAIGN if ours else ChatSource.OWNER
    if not await record_message(
        session,
        contact,
        direction=ChatDirection.OUT,
        source=source,
        mid=dm.mid,
        text=dm.text,
        now=ctx.now,
    ):
        return
    contact.last_outbound_at = ctx.now
    contact.messages_out += 1
    if not ours and ctx.config.pause_on_manual_reply:
        await start_human_mode(session, contact, ctx.config.handoff_hours, "manual_reply", ctx.now)


async def handle_message(
    session: AsyncSession,
    dm: IncomingDM,
    ctx: ChatContext,
    *,
    story_reply_handler: Any,
) -> int:
    """One message from a person. Returns how many things were queued (jobs or outbox)."""
    if dm.sender_id in {dm.owner_id, ctx.account_ig_id}:
        return 0
    contact, created = await get_or_create_contact(session, dm.sender_id, ctx.now)
    if contact.anonymized_at is not None:
        contact.anonymized_at = None  # they came back: a new conversation
    if not await record_message(
        session,
        contact,
        direction=ChatDirection.IN,
        source=ChatSource.USER,
        mid=dm.mid,
        text=dm.text,
        payload=dm.quick_reply_payload,
        now=ctx.now,
    ):
        return 0  # the same webhook delivered twice
    first_message = created or contact.messages_in == 0
    contact.last_inbound_at = ctx.now
    contact.messages_in += 1

    if in_human_mode(contact, ctx.now):
        return 0
    if dm.quick_reply_payload and await handle_option(session, contact, dm, ctx):
        return 1
    if dm.is_story_reply:
        if await story_reply_handler(dm):
            return 1
    elif await enqueue_direct_campaign(session, dm, ctx):
        return 1
    flow = match_flow(dm.text, ctx)
    if flow is not None and (step := first_step(flow)) is not None:
        return int(await enqueue_step(session, contact, flow, step, dm.mid, ctx.now))
    if first_message and ctx.config.welcome_flow_id is not None:
        welcome = next((f for f in ctx.flows if f.id == ctx.config.welcome_flow_id), None)
        if welcome is not None and (step := first_step(welcome)) is not None:
            return int(await enqueue_step(session, contact, welcome, step, dm.mid, ctx.now))
    if ctx.config.ai_enabled and ctx.ai_available and dm.text:
        return int(await enqueue_ai(session, contact, dm, ctx.now))
    return 0
