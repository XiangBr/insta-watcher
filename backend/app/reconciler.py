"""Safety net for comments the webhook never delivered. Narrow on purpose:

only active campaigns; only comments newer than both the lookback window and the campaign
itself; only if the keyword matches, no delivery exists and the owner has not replied yet;
at most RECONCILE_MAX_PER_SWEEP new ones per post. Comments hidden by Instagram's filters
may not show up in the API at all.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import utcnow
from app.dispatcher import IncomingComment, Outcome, active_campaigns, enqueue_comment
from app.matching import TriggerMatcher
from app.meta.client import MetaClient
from app.meta.errors import ConfirmedRejection, MetaError, RejectionKind
from app.models import AccountStatus, Origin
from app.ops import OpsLevel, OpsSource, record
from app.tokens import TokenDecryptError, load_token, mark_token_invalid

logger = logging.getLogger(__name__)

MAX_PAGES_PER_POST = 3
RECENT_MEDIA_FOR_ANY_POST = 10


def parse_timestamp(value: object) -> datetime | None:
    """Graph timestamps look like 2026-10-02T03:46:58+0000."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def owner_replied(comment: dict[str, Any], owner_id: str) -> bool:
    replies = (comment.get("replies") or {}).get("data") or []
    return any(str((reply.get("from") or {}).get("id")) == owner_id for reply in replies)


async def reconcile(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    meta: MetaClient,
    matcher: TriggerMatcher,
    now: datetime | None = None,
) -> int:
    """One sweep. Returns how many comments were queued."""
    now = now or utcnow()
    since = now - timedelta(hours=settings.reconcile_lookback_hours)
    async with session_factory() as session, session.begin():
        try:
            loaded = await load_token(session, settings)
        except TokenDecryptError as exc:
            await mark_token_invalid(session, str(exc))
            return 0
        if loaded is None or loaded[0].status != AccountStatus.ACTIVE:
            return 0
        account, token = loaded
        owner_id = account.ig_user_id
        campaigns = list(await active_campaigns(session))
    if not campaigns:
        return 0

    media_ids = {c.post_id for c in campaigns if c.post_id}
    try:
        if any(c.match_any_post for c in campaigns):
            for media in await meta.list_media(token, limit=RECENT_MEDIA_FOR_ANY_POST):
                created = parse_timestamp(media.get("timestamp"))
                if media.get("id") and (created is None or created >= since):
                    media_ids.add(str(media["id"]))
    except MetaError as exc:
        await _report(session_factory, exc)
        return 0

    queued = 0
    for media_id in sorted(media_ids):
        try:
            queued += await _sweep_post(
                session_factory, settings, meta, matcher, token, owner_id, media_id, since
            )
        except MetaError as exc:
            await _report(session_factory, exc)
            if isinstance(exc, ConfirmedRejection) and exc.kind == RejectionKind.TOKEN_INVALID:
                break
    return queued


async def _sweep_post(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    meta: MetaClient,
    matcher: TriggerMatcher,
    token: str,
    owner_id: str,
    media_id: str,
    since: datetime,
) -> int:
    queued = 0
    cursor: str | None = None
    for _ in range(MAX_PAGES_PER_POST):
        comments, cursor = await meta.list_comments(token, media_id, after=cursor)
        async with session_factory() as session, session.begin():
            campaigns = await active_campaigns(session)
            for raw in comments:
                created = parse_timestamp(raw.get("timestamp"))
                sender = raw.get("from") or {}
                if created is None or created < since or not raw.get("id") or not sender.get("id"):
                    continue
                if owner_replied(raw, owner_id):
                    continue
                # Never answer comments made before the campaign existed.
                eligible = [c for c in campaigns if c.created_at <= created]
                outcome = await enqueue_comment(
                    session,
                    IncomingComment(
                        comment_id=str(raw["id"]),
                        text=str(raw.get("text") or ""),
                        from_id=str(sender["id"]),
                        from_username=sender.get("username"),
                        media_id=media_id,
                        original_media_id=None,
                        owner_id=owner_id,
                    ),
                    origin=Origin.POLLING,
                    campaigns=eligible,
                    matcher=matcher,
                    account_ig_id=owner_id,
                )
                if outcome == Outcome.QUEUED:
                    queued += 1
                    if queued >= settings.reconcile_max_per_sweep:
                        return queued
        if cursor is None:
            break
    return queued


async def _report(session_factory: async_sessionmaker[AsyncSession], exc: MetaError) -> None:
    async with session_factory() as session, session.begin():
        if isinstance(exc, ConfirmedRejection) and exc.kind == RejectionKind.TOKEN_INVALID:
            await mark_token_invalid(session, exc.message)
            return
        record(session, OpsSource.RECONCILER, OpsLevel.WARNING, f"sweep failed: {exc}")
