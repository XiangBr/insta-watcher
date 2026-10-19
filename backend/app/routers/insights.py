"""Insights API. Reads only the tables the insights sync fills, so it never calls Meta."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import String, func, select

from app.api_models import (
    ActivityHour,
    Commenter,
    Demographics,
    InsightDayOut,
    InsightPost,
    InsightsActivityOut,
    InsightsAudience,
    InsightsOut,
    InsightsProfile,
    InsightsSyncOut,
    InsightsSyncStarted,
    KeywordCount,
)
from app.db import utcnow
from app.errors import ApiError
from app.insights import (
    SNAPSHOT_AUDIENCE,
    SNAPSHOT_PROFILE,
    SNAPSHOT_STATUS,
    media_kind,
    sync_insights,
)
from app.models import (
    Account,
    AccountStatus,
    Delivery,
    InsightComment,
    InsightDay,
    InsightMedia,
    InsightSnapshot,
)
from app.routers.admin import MetaDep, _aware, require_admin
from app.routers.public import SessionFactoryDep, SettingsDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/insights", dependencies=[Depends(require_admin)])

TOP_COMMENTERS = 15
TOP_KEYWORDS = 15


def _numbers(values: dict[str, Any] | None) -> dict[str, float]:
    return {
        k: float(v)
        for k, v in (values or {}).items()
        if isinstance(v, int | float) and not isinstance(v, bool)
    }


def _sync_out(row: InsightSnapshot | None, running: bool, interval: int) -> InsightsSyncOut:
    data: dict[str, Any] = row.data if row is not None and isinstance(row.data, dict) else {}
    finished = data.get("finished_at")
    return InsightsSyncOut(
        finished_at=datetime.fromisoformat(finished) if isinstance(finished, str) else None,
        running=running,
        partial=bool(data.get("partial")),
        missing_permission=bool(data.get("missing_permission")),
        errors=[str(e) for e in data.get("errors") or []],
        interval_seconds=interval,
    )


def _audience_out(row: InsightSnapshot | None) -> InsightsAudience | None:
    if row is None or not isinstance(row.data, dict):
        return None
    data = row.data

    def groups(key: str) -> Demographics | None:
        value = data.get(key)
        return Demographics.model_validate(value) if isinstance(value, dict) else None

    def flat(key: str) -> dict[str, float] | None:
        value = data.get(key)
        return _numbers(value) if isinstance(value, dict) else None

    return InsightsAudience(
        fetched_at=row.fetched_at,
        window_days=int(data.get("window_days") or 28),
        followers=groups("followers"),
        engaged=groups("engaged"),
        reach_by_follow_type=flat("reach_by_follow_type"),
        reach_by_product=flat("reach_by_product"),
        interactions_by_product=flat("interactions_by_product"),
        online_followers=flat("online_followers"),
    )


def _running(request: Request) -> bool:
    lock: asyncio.Lock | None = getattr(request.app.state, "insights_lock", None)
    return bool(lock and lock.locked())


@router.get("")
async def insights(
    request: Request, settings: SettingsDep, session_factory: SessionFactoryDep
) -> InsightsOut:
    """Everything the insights page shows, from the last sync. Filters run in the page."""
    async with session_factory() as session:
        media = (
            (await session.execute(select(InsightMedia).order_by(InsightMedia.posted_at.desc())))
            .scalars()
            .all()
        )
        days = (await session.execute(select(InsightDay).order_by(InsightDay.day))).scalars().all()
        status = await session.get(InsightSnapshot, SNAPSHOT_STATUS)
        profile = await session.get(InsightSnapshot, SNAPSHOT_PROFILE)
        audience = await session.get(InsightSnapshot, SNAPSHOT_AUDIENCE)
    return InsightsOut(
        sync=_sync_out(status, _running(request), settings.insights_sync_interval_seconds),
        utc_offset_minutes=settings.insights_utc_offset_minutes,
        profile=(
            InsightsProfile.model_validate(profile.data)
            if profile is not None and isinstance(profile.data, dict)
            else None
        ),
        posts=[
            InsightPost(
                id=m.media_id,
                posted_at=m.posted_at,
                media_type=m.media_type,
                kind=media_kind(m.media_type, m.product_type),
                caption=m.caption,
                permalink=m.permalink,
                thumbnail_url=m.thumbnail_url,
                media_url=m.media_url,
                like_count=m.like_count,
                comments_count=m.comments_count,
                metrics=_numbers(m.metrics),
                terms={k: int(v) for k, v in _numbers(m.terms).items()},
                insights_error=m.insights_error,
            )
            for m in media
        ],
        days=[
            InsightDayOut(
                day=d.day,
                followers_count=d.followers_count,
                complete=d.complete,
                metrics=_numbers(d.metrics),
            )
            for d in days
        ],
        audience=_audience_out(audience),
    )


@router.get("/activity")
async def activity(
    session_factory: SessionFactoryDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> InsightsActivityOut:
    """When people comment and who comes back, in a time range. Owner's comments excluded."""
    start = _aware(since, "since") or utcnow() - timedelta(days=30)
    end = _aware(until, "until") or utcnow()
    if end <= start:
        raise ApiError(422, "until must be after since")
    in_range = (InsightComment.commented_at >= start, InsightComment.commented_at < end)
    hour = func.substr(InsightComment.commented_at, 1, 13, type_=String())
    async with session_factory() as session:
        hours = (
            await session.execute(
                select(hour, func.count()).where(*in_range).group_by(hour).order_by(hour)
            )
        ).all()
        total = (
            await session.execute(select(func.count()).select_from(InsightComment).where(*in_range))
        ).scalar_one()
        people = (
            await session.execute(
                select(
                    InsightComment.author_id,
                    func.max(InsightComment.username),
                    func.count(),
                    func.count(func.distinct(InsightComment.media_id)),
                    func.max(InsightComment.commented_at),
                )
                .where(*in_range, InsightComment.author_id.is_not(None))
                .group_by(InsightComment.author_id)
                .order_by(func.count().desc())
            )
        ).all()
        keyword_rows = (
            await session.execute(
                select(Delivery.matched_keyword, func.count())
                .where(
                    Delivery.created_at >= start,
                    Delivery.created_at < end,
                    Delivery.matched_keyword.is_not(None),
                )
                .group_by(Delivery.matched_keyword)
                .order_by(func.count().desc())
                .limit(TOP_KEYWORDS)
            )
        ).all()
    return InsightsActivityOut(
        hours=[
            ActivityHour(hour=datetime.fromisoformat(f"{h}:00:00+00:00"), count=n) for h, n in hours
        ],
        total_comments=total,
        unique_commenters=len(people),
        returning_commenters=sum(1 for p in people if p[3] >= 2),
        top_commenters=[
            Commenter(
                username=name,
                comments=n,
                posts=posts,
                last_at=datetime.fromisoformat(last.replace("Z", "+00:00"))
                if isinstance(last, str)
                else last,
            )
            for _, name, n, posts, last in people[:TOP_COMMENTERS]
            if name
        ],
        keywords=[KeywordCount(keyword=k, count=n) for k, n in keyword_rows],
    )


@router.post("/sync", status_code=202)
async def start_sync(
    request: Request, settings: SettingsDep, session_factory: SessionFactoryDep, meta: MetaDep
) -> InsightsSyncStarted:
    """Start a sync now, in the background. It can take a few minutes on the first runs."""
    async with session_factory() as session:
        account = await session.get(Account, 1)
    if account is None or account.status != AccountStatus.ACTIVE:
        raise ApiError(409, "no active account: store a valid token first")
    lock: asyncio.Lock = request.app.state.insights_lock
    if lock.locked():
        return InsightsSyncStarted(started=False)

    async def run() -> None:
        async with lock:
            try:
                await sync_insights(session_factory, settings, meta)
            except Exception:
                logger.exception("insights sync started from the dashboard failed")

    request.app.state.insights_task = asyncio.create_task(run(), name="insights-sync")
    # Let the task take the lock before answering, so a quick second click sees it running.
    await asyncio.sleep(0)
    return InsightsSyncStarted(started=True)
