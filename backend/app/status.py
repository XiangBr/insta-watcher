"""Operational snapshot for /api/v1/status and `cli status`. No secrets, no user text."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import utcnow
from app.dispatcher import message_events
from app.meta.limits import RATE_WINDOW
from app.models import (
    Account,
    Campaign,
    Job,
    JobStatus,
    OpsEvent,
    SendEvent,
    WebhookEvent,
    WebhookStatus,
)
from app.ratelimit import PRIVATE_REPLY


def summarize_webhook_payload(payload: dict[str, Any], *, with_text: bool) -> list[dict[str, Any]]:
    """What each stored webhook event carried, for diagnosis. Comment text only on request."""
    items: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") in ("messages", "messaging_postbacks"):
                continue  # summarized below with its kind, whichever shape Meta used
            value = change.get("value") or {}
            item = {
                "field": change.get("field"),
                "comment_id": value.get("id"),
                "media_id": (value.get("media") or {}).get("id"),
                "from_username": (value.get("from") or {}).get("username"),
            }
            if with_text:
                item["text"] = value.get("text")
            items.append({k: v for k, v in item.items() if v is not None})
    for _owner, event in message_events(payload):
        postback = event.get("postback")
        if isinstance(postback, dict):
            # The payload is a string we chose when we sent the button, so it is safe to show.
            tap: dict[str, Any] = {"field": "messaging_postbacks", "kind": "postback"}
            if isinstance(postback.get("payload"), str):
                tap["payload"] = postback["payload"][:100]
            items.append(tap)
            continue
        body = event.get("message") or {}
        kind = "message"
        if body.get("is_echo"):
            kind = "echo"
        elif (body.get("reply_to") or {}).get("story"):
            kind = "story_reply"
        message_item: dict[str, Any] = {"field": "messages", "kind": kind}
        if with_text and isinstance(body.get("text"), str):
            message_item["text"] = body["text"]
        items.append(message_item)
    return items


async def collect_status(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utcnow()

    async def scalar(session: AsyncSession, query: Any) -> Any:
        return (await session.execute(query)).scalar_one_or_none()

    async with session_factory() as session:
        account = await session.get(Account, 1)
        last_webhook = await scalar(session, select(func.max(WebhookEvent.received_at)))
        pending_events = await scalar(
            session,
            select(func.count(WebhookEvent.id)).where(WebhookEvent.status == WebhookStatus.PENDING),
        )
        job_rows = await session.execute(
            select(Job.status, func.count(Job.id)).group_by(Job.status)
        )
        jobs = {status: count for status, count in job_rows.all()}
        sent_last_hour = await scalar(
            session,
            select(func.count(SendEvent.id)).where(
                SendEvent.kind == PRIVATE_REPLY, SendEvent.sent_at > now - RATE_WINDOW
            ),
        )
        active_campaigns = await scalar(
            session,
            select(func.count(Campaign.id)).where(
                Campaign.is_active.is_(True), Campaign.deleted_at.is_(None)
            ),
        )
        unresolved_errors = await scalar(
            session,
            select(func.count(OpsEvent.id)).where(
                OpsEvent.level == "ERROR", OpsEvent.resolved_at.is_(None)
            ),
        )

    token_days_left = None
    if account is not None and account.token_expires_at is not None:
        token_days_left = (account.token_expires_at - now).days
    return {
        "account": None
        if account is None
        else {
            "username": account.username,
            "ig_user_id": account.ig_user_id,
            "status": account.status,
            "sending_paused": account.sending_paused,
            "token_days_left": token_days_left,
            "token_expiry_confirmed": account.token_expiry_confirmed,
        },
        "active_campaigns": active_campaigns or 0,
        "last_webhook_at": last_webhook.isoformat() if last_webhook else None,
        "pending_webhook_events": pending_events or 0,
        "jobs": {
            "pending": jobs.get(JobStatus.PENDING, 0),
            "running": jobs.get(JobStatus.RUNNING, 0),
            "unconfirmed": jobs.get(JobStatus.UNCONFIRMED, 0),
            "failed": jobs.get(JobStatus.FAILED, 0),
        },
        "rate_limit": {"used": sent_last_hour or 0, "limit": settings.private_reply_hourly_limit},
        "unresolved_errors": unresolved_errors or 0,
    }
