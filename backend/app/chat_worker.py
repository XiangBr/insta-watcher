"""Sends the chat outbox: flow steps, links, follow-ups, handoff texts, the owner's replies from
the panel, and AI answers.

Same rules as the campaign worker. Right before each send it checks the message still makes
sense (the person is not with a human, the 24 h window is open, a follow-up's person has not
written since). send_started_at is written before calling Meta. An explicit rejection may be
retried; anything ambiguous is UNCONFIRMED and never sent again on its own.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import chat_ai
from app.chat import enqueue_outbox, in_human_mode, record_message, start_human_mode
from app.config import Settings
from app.db import utcnow
from app.flows import (
    DEFAULT_LINK_LABEL,
    DEFAULT_LINK_TEXT,
    ChatConfig,
    active_flows,
    find_step,
    load_chat_config,
    option_payload,
)
from app.meta.client import Button, LinkButton, MetaClient, QuickReply
from app.meta.errors import (
    AmbiguousFailure,
    ConfirmedRejection,
    MetaError,
    NotSent,
    RejectionKind,
)
from app.meta.limits import MAX_ATTEMPTS, MESSAGING_WINDOW, RETRY_BACKOFF
from app.models import (
    Account,
    AccountStatus,
    ChatDirection,
    ChatMessage,
    ChatOutbox,
    ChatSource,
    Contact,
    Flow,
    OutboxKind,
    OutboxStatus,
)
from app.openrouter import OpenRouterClient, OpenRouterError
from app.ops import OpsLevel, OpsSource, record
from app.tokens import TokenDecryptError, cipher_for, mark_token_invalid
from app.worker import render

logger = logging.getLogger(__name__)

LEASE = timedelta(minutes=3)  # an AI answer can take a while before the send
USERNAMES_PER_TICK = 5
USERNAME_RETRY = timedelta(hours=1)
ACCOUNT_BLOCKED_RETRY = timedelta(minutes=5)
CONCURRENCY = 3

SOURCE_OF = {
    OutboxKind.STEP: ChatSource.FLOW,
    OutboxKind.LINK: ChatSource.FLOW,
    OutboxKind.FOLLOWUP: ChatSource.FOLLOWUP,
    OutboxKind.AI_REPLY: ChatSource.AI,
}


class Skip(Exception):
    """This message should not go out any more. The reason is stored on the row."""


@dataclass
class Outgoing:
    text: str
    button: Button | None = None
    quick_replies: list[QuickReply] | None = None
    source: ChatSource = ChatSource.FLOW
    flow_id: int | None = None
    step_id: str | None = None
    cost_usd: float | None = None
    # After sending: hold automatic answers for this person (AI decided a human should reply).
    handoff: bool = False
    followup: dict[str, Any] | None = None


async def claim_due(sf: async_sessionmaker[AsyncSession], limit: int) -> list[int]:
    now = utcnow()
    due = (
        select(ChatOutbox.id)
        .where(ChatOutbox.status == OutboxStatus.PENDING, ChatOutbox.run_at <= now)
        .order_by(ChatOutbox.run_at, ChatOutbox.id)
        .limit(limit)
        .scalar_subquery()
    )
    async with sf() as session, session.begin():
        result = await session.execute(
            update(ChatOutbox)
            .where(ChatOutbox.id.in_(due), ChatOutbox.status == OutboxStatus.PENDING)
            .values(status=OutboxStatus.RUNNING, locked_until=now + LEASE, updated_at=now)
            .returning(ChatOutbox.id)
        )
        return list(result.scalars().all())


async def recover_stale(sf: async_sessionmaker[AsyncSession], *, at_boot: bool) -> int:
    """RUNNING with an expired lease (every RUNNING one at boot): back to PENDING if Meta was
    never called, UNCONFIRMED if it may have been."""
    now = utcnow()
    query = select(ChatOutbox).where(ChatOutbox.status == OutboxStatus.RUNNING)
    if not at_boot:
        query = query.where(ChatOutbox.locked_until < now)
    unconfirmed = 0
    async with sf() as session, session.begin():
        for row in await session.scalars(query):
            row.locked_until = None
            if row.send_started_at is None:
                row.status = OutboxStatus.PENDING
            else:
                row.status = OutboxStatus.UNCONFIRMED
                row.error = "interrupted after calling Meta; check the conversation"
                unconfirmed += 1
        if unconfirmed:
            record(
                session,
                OpsSource.WORKER,
                OpsLevel.WARNING,
                f"{unconfirmed} chat message(s) interrupted mid-call are now UNCONFIRMED",
            )
    return unconfirmed


class ChatWorker:
    def __init__(
        self,
        sf: async_sessionmaker[AsyncSession],
        settings: Settings,
        meta: MetaClient,
        router: OpenRouterClient | None,
    ) -> None:
        self._sf = sf
        self._settings = settings
        self._meta = meta
        self._router = router
        # Contacts whose @ lookup failed, and when: tried again after USERNAME_RETRY.
        self._username_tries: dict[int, datetime] = {}

    async def run_until_idle(self) -> int:
        await recover_stale(self._sf, at_boot=False)
        done = 0
        while True:
            ids = await claim_due(self._sf, CONCURRENCY)
            if not ids:
                await self.fill_usernames()
                return done
            await asyncio.gather(*(self._run_safely(i) for i in ids))
            done += len(ids)

    async def _run_safely(self, outbox_id: int) -> None:
        try:
            await self.process(outbox_id)
        except Exception:
            # Stays RUNNING; recovery decides from send_started_at.
            logger.exception("chat worker: outbox %s crashed", outbox_id)

    # ---- one message ---------------------------------------------------------------------

    async def process(self, outbox_id: int) -> None:
        now = utcnow()
        async with self._sf() as session, session.begin():
            row = await session.get(ChatOutbox, outbox_id)
            if row is None or row.status != OutboxStatus.RUNNING:
                return
            contact = await session.get(Contact, row.contact_id)
            token = await self._token(session)
            if token is None:
                row.status, row.run_at, row.locked_until = (
                    OutboxStatus.PENDING,
                    now + ACCOUNT_BLOCKED_RETRY,
                    None,
                )
                return
            config = await load_chat_config(session)
            flows = await active_flows(session)
            try:
                self._check(row, contact, now)
            except Skip as exc:
                self._close(row, OutboxStatus.CANCELLED, str(exc))
                return
            assert contact is not None  # noqa: S101 (checked above)
            kind, payload, contact_id = row.kind, dict(row.payload), contact.id
            recipient, username = contact.ig_user_id, contact.username
        if username is None:
            username = await self._lookup_username(token, contact_id, recipient)
        try:
            outgoing = await self._build(kind, payload, contact_id, username, config, flows)
        except Skip as exc:
            await self._finish(outbox_id, OutboxStatus.CANCELLED, str(exc))
            return
        except OpenRouterError as exc:
            await self._finish(outbox_id, OutboxStatus.FAILED, str(exc))
            async with self._sf() as session, session.begin():
                record(session, OpsSource.WORKER, OpsLevel.WARNING, f"chat AI failed: {exc}")
            return

        async with self._sf() as session, session.begin():
            stored = await session.get(ChatOutbox, outbox_id)
            assert stored is not None  # noqa: S101
            stored.send_started_at = utcnow()
            stored.attempts += 1
            attempts = stored.attempts
            if kind == OutboxKind.AI_REPLY:
                # Keep what the model wrote: a retry after NotSent must not pay for it again.
                stored.payload = {
                    **payload,
                    "text": outgoing.text,
                    "handoff": outgoing.handoff,
                    "cost_usd": outgoing.cost_usd,
                }
        try:
            message_id = await self._meta.send_message(
                token, recipient, outgoing.text, outgoing.button, outgoing.quick_replies
            )
        except AmbiguousFailure as exc:
            await self._finish(outbox_id, OutboxStatus.UNCONFIRMED, str(exc))
            return
        except NotSent as exc:
            await self._retry_or_fail(outbox_id, attempts, str(exc))
            return
        except ConfirmedRejection as exc:
            await self._rejected(outbox_id, exc, attempts)
            return
        await self._sent(outbox_id, contact_id, message_id, outgoing, config)

    async def fill_usernames(self) -> int:
        """The webhook carries no @, so people the bot never answered would stay nameless.
        Look up a few per tick, read only. A failed lookup waits USERNAME_RETRY."""
        now = utcnow()
        async with self._sf() as session:
            account = await session.get(Account, 1)
            if account is None or account.status != AccountStatus.ACTIVE:
                return 0
            try:
                token = cipher_for(self._settings).decrypt(account.token_encrypted)
            except TokenDecryptError:
                return 0
            rows = (
                await session.execute(
                    select(Contact.id, Contact.ig_user_id)
                    .where(Contact.username.is_(None), Contact.anonymized_at.is_(None))
                    .order_by(Contact.last_inbound_at.desc())
                    .limit(USERNAMES_PER_TICK * 4)
                )
            ).all()
        due = [
            (cid, uid)
            for cid, uid in rows
            if now - self._username_tries.get(cid, datetime.min.replace(tzinfo=now.tzinfo))
            >= USERNAME_RETRY
        ][:USERNAMES_PER_TICK]
        found = 0
        for contact_id, user_id in due:
            self._username_tries[contact_id] = now
            if await self._lookup_username(token, contact_id, user_id):
                self._username_tries.pop(contact_id, None)
                found += 1
        return found

    async def _lookup_username(self, token: str, contact_id: int, user_id: str) -> str | None:
        """Best effort, once per person: the webhook carries no @. Any failure leaves it empty."""
        try:
            profile = await self._meta.get_profile(token, user_id, "username")
        except MetaError as exc:
            logger.warning("chat: username lookup failed: %s", exc)
            return None
        username = profile.get("username")
        if not isinstance(username, str) or not username:
            return None
        async with self._sf() as session, session.begin():
            contact = await session.get(Contact, contact_id)
            if contact is not None:
                contact.username = username[:64].lower()
        return username

    async def _token(self, session: AsyncSession) -> str | None:
        account = await session.get(Account, 1)
        if account is None or account.status != AccountStatus.ACTIVE or account.sending_paused:
            return None
        try:
            return cipher_for(self._settings).decrypt(account.token_encrypted)
        except TokenDecryptError as exc:
            await mark_token_invalid(session, str(exc))
            return None

    @staticmethod
    def _check(row: ChatOutbox, contact: Contact | None, now: datetime) -> None:
        if contact is None or contact.anonymized_at is not None:
            raise Skip("contact gone")
        # TEXT is the handoff message or the owner's own reply: it goes out in human mode.
        if row.kind != OutboxKind.TEXT and in_human_mode(contact, now):
            raise Skip("with a human")
        if contact.last_inbound_at is None or now - contact.last_inbound_at >= MESSAGING_WINDOW:
            raise Skip("24 h messaging window closed")
        if (
            row.kind == OutboxKind.FOLLOWUP
            and row.payload.get("only_if_no_reply", True)
            and contact.last_inbound_at > row.created_at
        ):
            raise Skip("the person wrote after the step")

    async def _build(
        self,
        kind: str,
        payload: dict[str, Any],
        contact_id: int,
        username: str | None,
        config: ChatConfig,
        flows: list[Flow],
    ) -> Outgoing:
        if kind == OutboxKind.STEP:
            flow = next((f for f in flows if f.id == payload.get("flow_id")), None)
            step = find_step(flow, str(payload.get("step_id"))) if flow else None
            if flow is None or step is None:
                raise Skip("flow switched off or step removed")
            button: Button | None = None
            if step.link_url:
                button = LinkButton(title=step.link_label or DEFAULT_LINK_LABEL, url=step.link_url)
            quick = [
                QuickReply(title=o.label, payload=option_payload(flow.id, step.id, i))
                for i, o in enumerate(step.options)
            ]
            return Outgoing(
                text=render(step.message, username),
                button=button,
                quick_replies=quick or None,
                flow_id=flow.id,
                step_id=step.id,
                followup=step.followup.model_dump() if step.followup else None,
            )
        if kind == OutboxKind.LINK:
            return Outgoing(
                text=render(str(payload.get("text") or DEFAULT_LINK_TEXT), username),
                button=LinkButton(
                    title=str(payload.get("label") or DEFAULT_LINK_LABEL), url=str(payload["url"])
                ),
                flow_id=payload.get("flow_id"),
                step_id=payload.get("step_id"),
            )
        if kind in (OutboxKind.TEXT, OutboxKind.FOLLOWUP):
            source = (
                ChatSource(payload.get("source", ChatSource.OWNER))
                if kind == OutboxKind.TEXT
                else ChatSource.FOLLOWUP
            )
            return Outgoing(
                text=render(str(payload.get("text") or ""), username),
                source=source,
                flow_id=payload.get("flow_id"),
                step_id=payload.get("step_id"),
            )
        return await self._ai(payload, contact_id, config, flows)

    async def _ai(
        self, payload: dict[str, Any], contact_id: int, config: ChatConfig, flows: list[Flow]
    ) -> Outgoing:
        if "text" in payload:  # written on an earlier attempt that never reached Meta
            handoff = bool(payload.get("handoff"))
            return Outgoing(
                text=config.handoff_message if handoff else str(payload["text"]),
                source=ChatSource.HANDOFF if handoff else ChatSource.AI,
                handoff=handoff,
                cost_usd=payload.get("cost_usd"),
            )
        if self._router is None or not config.ai_enabled:
            raise Skip("AI switched off")
        now = utcnow()
        async with self._sf() as session:
            per_hour = await session.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .where(
                    ChatMessage.source == ChatSource.AI,
                    ChatMessage.created_at >= now - timedelta(hours=1),
                )
            )
            per_contact = await session.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .where(
                    ChatMessage.contact_id == contact_id,
                    ChatMessage.source == ChatSource.AI,
                    ChatMessage.created_at >= now - timedelta(days=1),
                )
            )
            history = list(
                await session.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.contact_id == contact_id)
                    .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                    .limit(chat_ai.HISTORY_MESSAGES)
                )
            )[::-1]
            account = await session.get(Account, 1)
        if (per_hour or 0) >= config.ai_max_per_hour:
            async with self._sf() as session, session.begin():
                record(
                    session,
                    OpsSource.WORKER,
                    OpsLevel.WARNING,
                    f"chat AI hourly limit ({config.ai_max_per_hour}) reached",
                )
            raise Skip("AI hourly limit reached")
        if (per_contact or 0) >= config.ai_max_per_contact_day:
            # A long conversation: time for a person.
            return Outgoing(text=config.handoff_message, source=ChatSource.HANDOFF, handoff=True)
        messages = chat_ai.build_messages(
            config, account.username if account else "", flows, history
        )
        model = config.ai_model or self._settings.chat_ai_model
        result = await chat_ai.generate(self._router, model, messages)
        if result.handoff:
            return Outgoing(
                text=config.handoff_message,
                source=ChatSource.HANDOFF,
                handoff=True,
                cost_usd=result.cost_usd,
            )
        return Outgoing(text=result.text, source=ChatSource.AI, cost_usd=result.cost_usd)

    # ---- outcomes ------------------------------------------------------------------------

    async def _sent(
        self,
        outbox_id: int,
        contact_id: int,
        message_id: str,
        outgoing: Outgoing,
        config: ChatConfig,
    ) -> None:
        now = utcnow()
        async with self._sf() as session, session.begin():
            row = await session.get(ChatOutbox, outbox_id)
            contact = await session.get(Contact, contact_id)
            assert row is not None and contact is not None  # noqa: S101
            row.status, row.sent_at, row.message_id = OutboxStatus.SENT, now, message_id
            row.locked_until, row.error = None, None
            if await record_message(
                session,
                contact,
                direction=ChatDirection.OUT,
                source=outgoing.source,
                mid=message_id,
                text=outgoing.text,
                flow_id=outgoing.flow_id,
                step_id=outgoing.step_id,
                cost_usd=outgoing.cost_usd,
                now=now,
            ):
                contact.messages_out += 1
            else:
                # The echo got here first and was taken as a campaign message: it is ours.
                await session.execute(
                    update(ChatMessage)
                    .where(ChatMessage.mid == message_id)
                    .values(
                        source=outgoing.source, flow_id=outgoing.flow_id, step_id=outgoing.step_id
                    )
                )
            contact.last_outbound_at = now
            if outgoing.handoff:
                await start_human_mode(session, contact, config.handoff_hours, "ai_handoff", now)
            if outgoing.followup:
                await enqueue_outbox(
                    session,
                    contact,
                    OutboxKind.FOLLOWUP,
                    {
                        "text": outgoing.followup["message"],
                        "only_if_no_reply": outgoing.followup.get("only_if_no_reply", True),
                        "flow_id": outgoing.flow_id,
                        "step_id": outgoing.step_id,
                    },
                    f"followup:{outbox_id}",
                    now + timedelta(minutes=int(outgoing.followup["delay_minutes"])),
                    now=now,
                )

    async def _finish(self, outbox_id: int, status: OutboxStatus, error: str | None) -> None:
        async with self._sf() as session, session.begin():
            row = await session.get(ChatOutbox, outbox_id)
            if row is not None:
                self._close(row, status, error)

    @staticmethod
    def _close(row: ChatOutbox, status: OutboxStatus, error: str | None) -> None:
        row.status, row.error, row.locked_until = status, error, None

    async def _retry_or_fail(self, outbox_id: int, attempts: int, reason: str) -> None:
        if attempts >= MAX_ATTEMPTS:
            await self._finish(outbox_id, OutboxStatus.FAILED, reason)
            return
        async with self._sf() as session, session.begin():
            row = await session.get(ChatOutbox, outbox_id)
            if row is None:
                return
            row.status = OutboxStatus.PENDING
            row.run_at = utcnow() + RETRY_BACKOFF[attempts - 1]
            row.send_started_at, row.locked_until, row.error = None, None, reason

    async def _rejected(self, outbox_id: int, exc: ConfirmedRejection, attempts: int) -> None:
        if exc.kind == RejectionKind.TOKEN_INVALID:
            async with self._sf() as session, session.begin():
                await mark_token_invalid(session, exc.message)
                row = await session.get(ChatOutbox, outbox_id)
                if row is not None:
                    row.status = OutboxStatus.PENDING
                    row.run_at = utcnow() + ACCOUNT_BLOCKED_RETRY
                    row.attempts = max(0, row.attempts - 1)
                    row.send_started_at, row.locked_until, row.error = None, None, str(exc)
            return
        if exc.kind == RejectionKind.RATE_LIMITED:
            await self._retry_or_fail(outbox_id, attempts, str(exc))
            return
        await self._finish(outbox_id, OutboxStatus.FAILED, str(exc))
