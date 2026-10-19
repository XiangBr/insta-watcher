"""Hourly housekeeping: retention, rate and login ledgers pruning, token refresh."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import utcnow
from app.login_throttle import prune_login_attempts
from app.meta.client import MetaClient
from app.models import (
    ChatMessage,
    ChatOutbox,
    Contact,
    Delivery,
    InsightComment,
    OpsEvent,
    OutboxStatus,
    PostbackReceipt,
    WebhookEvent,
)
from app.ratelimit import prune_send_events
from app.tokens import refresh_token_if_due

logger = logging.getLogger(__name__)

ANONYMIZED = "anon"


@dataclass(frozen=True)
class RetentionResult:
    webhook_events_deleted: int
    ops_events_deleted: int
    deliveries_anonymized: int
    postback_receipts_deleted: int = 0
    commenters_anonymized: int = 0
    chat_messages_cleared: int = 0
    contacts_anonymized: int = 0


async def apply_retention(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    now: datetime | None = None,
) -> RetentionResult:
    now = now or utcnow()
    webhook_cutoff = now - timedelta(days=settings.retention_webhook_days)
    ops_cutoff = now - timedelta(days=settings.retention_ops_days)
    delivery_cutoff = now - timedelta(days=settings.retention_delivery_days)
    async with session_factory() as session, session.begin():
        webhooks = await session.execute(
            delete(WebhookEvent).where(WebhookEvent.received_at < webhook_cutoff)
        )
        ops = await session.execute(delete(OpsEvent).where(OpsEvent.created_at < ops_cutoff))
        # A receipt only has to outlive Meta's retries of the same tap, so it follows the
        # webhook payload's short retention.
        receipts = await session.execute(
            delete(PostbackReceipt).where(PostbackReceipt.created_at < webhook_cutoff)
        )
        expired = (
            (
                await session.execute(
                    select(Delivery).where(
                        Delivery.created_at < delivery_cutoff, Delivery.anonymized_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        for delivery in expired:
            # Keep the row so totals stay right; drop everything that points to a person.
            delivery.text = None
            delivery.actor_username = None
            delivery.actor_id = ANONYMIZED
            delivery.gate_user_id = None
            delivery.source_id = f"{ANONYMIZED}:{delivery.id}"
            delivery.anonymized_at = now
        # Insights keep when each comment happened, but not who wrote it.
        commenters = await session.execute(
            update(InsightComment)
            .where(
                InsightComment.commented_at < delivery_cutoff,
                InsightComment.author_id.is_not(None) | InsightComment.username.is_not(None),
            )
            .values(author_id=None, username=None)
        )
        # Chat: the text of old messages goes, the row stays for the counts. A person quiet
        # for the whole period loses the link to their Instagram id.
        chat_messages = await session.execute(
            update(ChatMessage)
            .where(ChatMessage.created_at < delivery_cutoff, ChatMessage.text.is_not(None))
            .values(text=None, payload=None)
        )
        await session.execute(
            delete(ChatOutbox).where(
                ChatOutbox.created_at < delivery_cutoff,
                ChatOutbox.status.not_in((OutboxStatus.PENDING, OutboxStatus.RUNNING)),
            )
        )
        quiet = await session.scalars(
            select(Contact).where(
                Contact.anonymized_at.is_(None),
                func.coalesce(Contact.last_inbound_at, Contact.first_seen_at) < delivery_cutoff,
            )
        )
        contacts_anonymized = 0
        for contact in quiet:
            contact.ig_user_id = f"{ANONYMIZED}:{contact.id}"
            contact.username = None
            contact.tags = []
            contact.human_until = None
            contact.anonymized_at = now
            contacts_anonymized += 1
    result = RetentionResult(
        webhook_events_deleted=webhooks.rowcount or 0,  # type: ignore[attr-defined]
        ops_events_deleted=ops.rowcount or 0,  # type: ignore[attr-defined]
        deliveries_anonymized=len(expired),
        postback_receipts_deleted=receipts.rowcount or 0,  # type: ignore[attr-defined]
        commenters_anonymized=commenters.rowcount or 0,  # type: ignore[attr-defined]
        chat_messages_cleared=chat_messages.rowcount or 0,  # type: ignore[attr-defined]
        contacts_anonymized=contacts_anonymized,
    )
    if any(
        (
            result.webhook_events_deleted,
            result.ops_events_deleted,
            len(expired),
            result.commenters_anonymized,
            result.chat_messages_cleared,
            result.contacts_anonymized,
        )
    ):
        logger.info("retention: %s", result)
    return result


async def run_maintenance(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    meta: MetaClient,
) -> None:
    await apply_retention(session_factory, settings)
    await prune_send_events(session_factory)
    await prune_login_attempts(session_factory)
    await refresh_token_if_due(session_factory, settings, meta)
