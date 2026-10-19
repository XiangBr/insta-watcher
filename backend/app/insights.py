"""Insights sync: a read-only copy of what Meta reports about the account, kept in SQLite.

The insights page reads only from these tables, so opening it never calls Meta. The sync
runs every INSIGHTS_SYNC_INTERVAL_SECONDS and spends at most INSIGHTS_MAX_CALLS_PER_SYNC
calls; the first runs backfill history a slice at a time.

Account and media insights need the `instagram_business_manage_insights` permission. Without
it the posts, likes, comments and comment activity still work (they come from the basic and
comments permissions), and the page says what is missing.

Nothing here sends anything. Comment text is read to count words and then dropped.
"""

import logging
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import utcnow
from app.matching import normalize
from app.meta.client import MetaClient
from app.meta.errors import ConfirmedRejection, MetaError, RejectionKind
from app.models import (
    AccountStatus,
    InsightComment,
    InsightDay,
    InsightMedia,
    InsightSnapshot,
)
from app.reconciler import parse_timestamp
from app.tokens import TokenDecryptError, load_token

logger = logging.getLogger(__name__)

# ---- what is asked of Meta ---------------------------------------------------------------

PROFILE_FIELDS = (
    "user_id,username,name,biography,website,followers_count,follows_count,media_count,"
    "profile_picture_url"
)
MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,timestamp,permalink,thumbnail_url,media_url,"
    "like_count,comments_count"
)
# media_product_type is not listed for Instagram Login in every version of the docs.
MEDIA_FIELDS_FALLBACK = MEDIA_FIELDS.replace("media_product_type,", "")

# Per media kind, because asking a reel for a feed-only metric fails the whole call.
MEDIA_METRICS: dict[str, tuple[str, ...]] = {
    "FEED": (
        "reach",
        "views",
        "likes",
        "comments",
        "saved",
        "shares",
        "total_interactions",
        "follows",
        "profile_visits",
        "profile_activity",
    ),
    "REELS": (
        "reach",
        "views",
        "likes",
        "comments",
        "saved",
        "shares",
        "total_interactions",
        "ig_reels_avg_watch_time",
        "ig_reels_video_view_total_time",
        "reels_skip_rate",
    ),
}
DAY_METRICS = (
    "reach",
    "views",
    "accounts_engaged",
    "total_interactions",
    "likes",
    "comments",
    "saves",
    "shares",
    "replies",
    "profile_links_taps",
)
DEMOGRAPHIC_BREAKDOWNS = ("age", "gender", "city", "country")
AUDIENCE_WINDOW_DAYS = 28
AUDIENCE_MAX_AGE = timedelta(hours=24)
# Meta may revise a day's numbers for up to 48 hours.
DAY_SETTLE = timedelta(hours=48)
RECENT_DAYS_FIRST = 7
COMMENT_PAGES_PER_MEDIA = 10
TERMS_KEPT_PER_MEDIA = 40
# Our own counts, kept in the media metrics next to Meta's.
LOCAL_METRICS = ("comment_mentions", "comment_questions")

SNAPSHOT_PROFILE = "profile"
SNAPSHOT_AUDIENCE = "audience"
SNAPSHOT_STATUS = "sync_status"
SNAPSHOT_UNSUPPORTED = "unsupported_metrics"

# Words that say nothing about the content. Portuguese first (the owner's audience), plus
# the English ones that show up in any comment section. Already normalized (no accents).
STOPWORDS = frozenset(
    """
    a ao aos aquela aquele aquilo as ate com como da das de dela dele deles demais depois do
    dos e ela elas ele eles em entao entre era essa esse esta estao estou eu foi ha isso
    isto ja la lhe mais mas me mesmo meu minha muito na nao nas nem no nos nossa nosso num
    numa o os ou para pela pelas pelo pelos por pra pro qual quando que quem se sem ser seu
    sua so sao tambem te tem tenho ter teu tu tua um uma voce voces vc vcs ta to tava aqui
    ai ali sim ne kkk kkkk kkkkk kkkkkk haha hahaha rs rsrs q pq tbm tb hj vou vai fica
    the and you your for that this with are was but not have just its all get
    """.split()
)
_MENTION = re.compile(r"@[\w.]+")


class BudgetSpent(Exception):
    """This run used its calls. The rest waits for the next run."""


class MissingPermission(Exception):
    """The token has no instagram_business_manage_insights."""


@dataclass
class SyncResult:
    calls: int = 0
    media_listed: int = 0
    media_refreshed: int = 0
    days_fetched: int = 0
    comments_added: int = 0
    audience_refreshed: bool = False
    missing_permission: bool = False
    partial: bool = False
    errors: list[str] = field(default_factory=list)


def is_permission_error(exc: MetaError) -> bool:
    # Code 10 also means "not enough viewers", so the message has to say it too.
    return (
        isinstance(exc, ConfirmedRejection)
        and exc.kind == RejectionKind.PERMANENT
        and exc.code in (10, 200)
        and "permission" in exc.message.lower()
    )


def media_kind(media_type: str | None, product_type: str | None) -> str:
    """REELS or FEED. Stories never show up in me/media."""
    if product_type in ("REELS", "CLIPS"):
        return "REELS"
    if product_type:
        return "FEED"
    # Without media_product_type: every video posted since 2022 is a reel.
    return "REELS" if media_type in ("VIDEO", "REELS") else "FEED"


def comment_terms(text: str) -> Counter[str]:
    words = normalize(_MENTION.sub(" ", text)).split()
    return Counter(w for w in words if len(w) >= 3 and not w.isdigit() and w not in STOPWORDS)


def merge_terms(current: dict[str, Any], added: Counter[str]) -> dict[str, int]:
    merged: Counter[str] = Counter({k: int(v) for k, v in current.items()})
    merged.update(added)
    return dict(merged.most_common(TERMS_KEPT_PER_MEDIA))


def local_day(moment: datetime, offset_minutes: int) -> date:
    return (moment.astimezone(UTC) + timedelta(minutes=offset_minutes)).date()


def day_bounds(day: date, offset_minutes: int) -> tuple[datetime, datetime]:
    """UTC start and end of a local day."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(minutes=offset_minutes)
    return start, start + timedelta(days=1)


def _pacific_offset_hours(day: date) -> int:
    """US Pacific offset (-7 in daylight saving, -8 otherwise), by date only."""
    march = date(day.year, 3, 1)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7 + 7)  # second Sunday
    november = date(day.year, 11, 1)
    dst_end = november + timedelta(days=(6 - november.weekday()) % 7)  # first Sunday
    return -7 if dst_start <= day < dst_end else -8


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def parse_media_insights(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in body.get("data") or []:
        name = item.get("name")
        if not isinstance(name, str):
            continue
        values = item.get("values") or []
        value = values[0].get("value") if values and isinstance(values[0], dict) else None
        if value is None and isinstance(item.get("total_value"), dict):
            value = item["total_value"].get("value")
        if (n := _number(value)) is not None:
            out[name] = n
        elif isinstance(value, dict):  # profile_activity and other breakdown-like values
            out[name] = {str(k): v for k, v in value.items() if _number(v) is not None}
        breakdowns = (
            (item.get("total_value") or {}).get("breakdowns")
            if isinstance(item.get("total_value"), dict)
            else None
        )
        if breakdowns:
            out[f"{name}_breakdown"] = parse_breakdown(item)
    return out


def parse_totals(body: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in body.get("data") or []:
        total = item.get("total_value")
        if isinstance(item.get("name"), str) and isinstance(total, dict):
            if (n := _number(total.get("value"))) is not None:
                out[item["name"]] = n
    return out


def parse_breakdown(item: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    total = item.get("total_value") or {}
    for breakdown in total.get("breakdowns") or []:
        for result in breakdown.get("results") or []:
            key = ", ".join(str(v) for v in result.get("dimension_values") or [])
            if (n := _number(result.get("value"))) is not None and key:
                out[key] = out.get(key, 0) + n
    return out


def first_breakdown(body: dict[str, Any]) -> dict[str, float]:
    data = body.get("data") or []
    return parse_breakdown(data[0]) if data and isinstance(data[0], dict) else {}


def parse_online_followers(body: dict[str, Any], offset_minutes: int) -> dict[str, float] | None:
    """Average online followers per local hour. Meta keys the hours in US Pacific time
    (verificar): they are moved to INSIGHTS_UTC_OFFSET_MINUTES here."""
    data = body.get("data") or []
    if not data:
        return None
    sums: dict[int, float] = dict.fromkeys(range(24), 0.0)
    days = 0
    for entry in data[0].get("values") or []:
        value, end = entry.get("value"), parse_timestamp(entry.get("end_time"))
        if not isinstance(value, dict) or not value or end is None:
            continue
        days += 1
        shift = offset_minutes / 60 - _pacific_offset_hours(end.date())
        for hour, count in value.items():
            if (n := _number(count)) is not None and str(hour).isdigit():
                sums[int((int(hour) + shift) % 24)] += n
    if not days:
        return None
    return {str(h): round(sums[h] / days, 1) for h in range(24)}


# ---- the sync ----------------------------------------------------------------------------


class _Run:
    def __init__(
        self,
        sf: async_sessionmaker[AsyncSession],
        settings: Settings,
        meta: MetaClient,
        token: str,
        owner_id: str,
        now: datetime,
        unsupported: dict[str, list[str]],
    ) -> None:
        self.sf = sf
        self.settings = settings
        self.meta = meta
        self.token = token
        self.owner_id = owner_id
        self.now = now
        self.unsupported = {k: set(v) for k, v in unsupported.items()}
        self.result = SyncResult()
        self.offset = settings.insights_utc_offset_minutes

    async def call(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if self.result.calls >= self.settings.insights_max_calls_per_sync:
            raise BudgetSpent
        self.result.calls += 1
        return await self.meta.get(self.token, path, params)

    async def insights_call(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            return await self.call(path, params)
        except MetaError as exc:
            if is_permission_error(exc):
                raise MissingPermission from None
            raise

    async def metrics_with_fallback(
        self, scope: str, path: str, metrics: Iterable[str], params: dict[str, str]
    ) -> dict[str, Any]:
        """Ask for all metrics at once. If Meta refuses the set, ask one by one and remember
        the ones it refuses, so the next runs leave them out."""
        wanted = [m for m in metrics if m not in self.unsupported.get(scope, set())]
        if not wanted:
            return {}
        try:
            body = await self.insights_call(path, {**params, "metric": ",".join(wanted)})
            return {"__body__": body}
        except ConfirmedRejection as exc:
            if exc.kind != RejectionKind.PERMANENT or len(wanted) == 1:
                raise
        merged: list[dict[str, Any]] = []
        for metric in wanted:
            try:
                body = await self.insights_call(path, {**params, "metric": metric})
            except ConfirmedRejection as exc:
                if exc.kind != RejectionKind.PERMANENT:
                    raise
                self.unsupported.setdefault(scope, set()).add(metric)
                continue
            merged.extend(body.get("data") or [])
        return {"__body__": {"data": merged}}

    # -- profile and posts --

    async def profile(self) -> None:
        body = await self.call("me", {"fields": PROFILE_FIELDS})
        today = local_day(self.now, self.offset).isoformat()
        followers = _number(body.get("followers_count"))
        async with self.sf() as session, session.begin():
            await _put_snapshot(session, SNAPSHOT_PROFILE, body, self.now)
            if followers is not None:
                row = await session.get(InsightDay, today)
                if row is None:
                    row = InsightDay(day=today, metrics={}, complete=False, fetched_at=self.now)
                    session.add(row)
                row.followers_count = int(followers)

    async def list_media(self) -> None:
        fields = MEDIA_FIELDS
        after: str | None = None
        seen: list[dict[str, Any]] = []
        complete = False
        while len(seen) < self.settings.insights_max_media:
            if self.result.calls >= self.settings.insights_max_calls_per_sync:
                raise BudgetSpent
            try:
                self.result.calls += 1
                items, after = await self.meta.list_media_page(
                    self.token, fields, limit=50, after=after
                )
            except ConfirmedRejection as exc:
                if fields != MEDIA_FIELDS or exc.kind != RejectionKind.PERMANENT or seen:
                    raise
                fields = MEDIA_FIELDS_FALLBACK
                continue
            seen.extend(i for i in items if i.get("id") and parse_timestamp(i.get("timestamp")))
            if after is None:
                complete = True
                break
        async with self.sf() as session, session.begin():
            for item in seen[: self.settings.insights_max_media]:
                await _upsert_media(session, item, self.now)
            if complete:
                # A post missing from a full listing was deleted on Instagram.
                gone = select(InsightMedia.media_id).where(InsightMedia.listed_at < self.now)
                await session.execute(
                    delete(InsightComment).where(InsightComment.media_id.in_(gone))
                )
                await session.execute(delete(InsightMedia).where(InsightMedia.listed_at < self.now))
        self.result.media_listed = len(seen)

    # -- account days --

    def days_to_fetch(self, known: dict[str, bool]) -> list[date]:
        today = local_day(self.now, self.offset)
        out = []
        for back in range(self.settings.insights_backfill_days):
            day = today - timedelta(days=back)
            if not known.get(day.isoformat(), False):
                out.append(day)
        return out

    async def fetch_day(self, day: date) -> None:
        start, end = day_bounds(day, self.offset)
        params = {
            "period": "day",
            "metric_type": "total_value",
            "since": str(int(start.timestamp())),
            "until": str(int(end.timestamp())),
        }
        got = await self.metrics_with_fallback("account_day", "me/insights", DAY_METRICS, params)
        metrics: dict[str, Any] = parse_totals(got.get("__body__", {}))
        if "follows_and_unfollows" not in self.unsupported.get("account_day", set()):
            try:
                body = await self.insights_call(
                    "me/insights",
                    {**params, "metric": "follows_and_unfollows", "breakdown": "follow_type"},
                )
                split = first_breakdown(body)
                if split:
                    metrics["follows"] = split.get("FOLLOWER", 0)
                    metrics["unfollows"] = split.get("NON_FOLLOWER", 0)
            except ConfirmedRejection as exc:
                if exc.kind != RejectionKind.PERMANENT:
                    raise
                self.unsupported.setdefault("account_day", set()).add("follows_and_unfollows")
        async with self.sf() as session, session.begin():
            row = await session.get(InsightDay, day.isoformat())
            if row is None:
                row = InsightDay(day=day.isoformat(), metrics={}, complete=False)
                session.add(row)
            row.metrics = metrics
            row.complete = end + DAY_SETTLE <= self.now
            row.fetched_at = self.now
        self.result.days_fetched += 1

    async def account_days(self, *, recent_only: bool) -> None:
        async with self.sf() as session:
            rows = (await session.execute(select(InsightDay.day, InsightDay.complete))).all()
        # Only fetch_day marks a day complete (the profile adds rows that are not).
        known = {d: bool(c) for d, c in rows}
        days = self.days_to_fetch(known)
        today = local_day(self.now, self.offset)
        if recent_only:
            days = [d for d in days if (today - d).days < RECENT_DAYS_FIRST]
        for day in days:
            await self.fetch_day(day)

    # -- media --

    def media_due(self, media: InsightMedia) -> bool:
        if media.insights_fetched_at is None:
            return True
        age = self.now - media.posted_at
        since = self.now - media.insights_fetched_at
        if age < timedelta(days=7):
            return since >= timedelta(hours=2)
        if age < timedelta(days=60):
            return since >= timedelta(hours=24)
        return since >= timedelta(days=14)

    async def media_insights(self) -> None:
        async with self.sf() as session:
            rows = (
                (
                    await session.execute(
                        select(InsightMedia).order_by(InsightMedia.posted_at.desc())
                    )
                )
                .scalars()
                .all()
            )
        due = [m for m in rows if self.media_due(m)]
        # Never fetched first (newest first), then the stalest.
        due.sort(
            key=lambda m: (
                (1, m.insights_fetched_at.timestamp())
                if m.insights_fetched_at
                else (0, -m.posted_at.timestamp())
            )
        )
        for media in due:
            kind = media_kind(media.media_type, media.product_type)
            error: str | None = None
            metrics: dict[str, Any] = {}
            try:
                got = await self.metrics_with_fallback(
                    f"media_{kind}",
                    f"{media.media_id}/insights",
                    MEDIA_METRICS[kind],
                    {"period": "lifetime"},
                )
                metrics = parse_media_insights(got.get("__body__", {}))
            except ConfirmedRejection as exc:
                if exc.kind == RejectionKind.TOKEN_INVALID:
                    raise
                # Posts from before the account became professional have no insights.
                error = str(exc)
            async with self.sf() as session, session.begin():
                row = await session.get(InsightMedia, media.media_id)
                if row is None:
                    continue
                local = {k: v for k, v in (row.metrics or {}).items() if k in LOCAL_METRICS}
                if error is None:
                    row.metrics = {**metrics, **local}
                row.insights_error = error
                row.insights_fetched_at = self.now
            self.result.media_refreshed += 1

    # -- audience --

    async def audience(self) -> None:
        async with self.sf() as session:
            current = await session.get(InsightSnapshot, SNAPSHOT_AUDIENCE)
        if current is not None and self.now - current.fetched_at < AUDIENCE_MAX_AGE:
            return
        data: dict[str, Any] = {"window_days": AUDIENCE_WINDOW_DAYS}
        for metric, key in (
            ("follower_demographics", "followers"),
            ("engaged_audience_demographics", "engaged"),
        ):
            groups: dict[str, Any] = {}
            for breakdown in DEMOGRAPHIC_BREAKDOWNS:
                groups[breakdown] = await self._optional_breakdown(
                    {
                        "metric": metric,
                        "period": "lifetime",
                        "metric_type": "total_value",
                        "timeframe": "this_month",
                        "breakdown": breakdown,
                    }
                )
            data[key] = groups if any(groups.values()) else None
        since = str(int((self.now - timedelta(days=AUDIENCE_WINDOW_DAYS)).timestamp()))
        until = str(int(self.now.timestamp()))
        window = {"period": "day", "metric_type": "total_value", "since": since, "until": until}
        data["reach_by_follow_type"] = await self._optional_breakdown(
            {**window, "metric": "reach", "breakdown": "follow_type"}
        )
        data["reach_by_product"] = await self._optional_breakdown(
            {**window, "metric": "reach", "breakdown": "media_product_type"}
        )
        data["interactions_by_product"] = await self._optional_breakdown(
            {**window, "metric": "total_interactions", "breakdown": "media_product_type"}
        )
        data["online_followers"] = None
        try:
            week = str(int((self.now - timedelta(days=7)).timestamp()))
            body = await self.insights_call(
                "me/insights",
                {"metric": "online_followers", "period": "lifetime", "since": week, "until": until},
            )
            data["online_followers"] = parse_online_followers(body, self.offset)
        except ConfirmedRejection as exc:
            if exc.kind != RejectionKind.PERMANENT:
                raise
        async with self.sf() as session, session.begin():
            await _put_snapshot(session, SNAPSHOT_AUDIENCE, data, self.now)
        self.result.audience_refreshed = True

    async def _optional_breakdown(self, params: dict[str, str]) -> dict[str, float] | None:
        """Under 100 followers (or engagements) Meta answers empty or with an error."""
        try:
            return first_breakdown(await self.insights_call("me/insights", params)) or None
        except ConfirmedRejection as exc:
            if exc.kind != RejectionKind.PERMANENT:
                raise
            return None

    # -- comments --

    async def comments(self) -> None:
        cutoff = self.now - timedelta(days=self.settings.insights_comment_days)
        async with self.sf() as session:
            rows = (
                (
                    await session.execute(
                        select(InsightMedia)
                        .where(InsightMedia.posted_at >= cutoff)
                        .order_by(InsightMedia.posted_at.desc())
                    )
                )
                .scalars()
                .all()
            )
        for media in rows:
            if (media.comments_count or 0) == (media.comments_seen_count or 0):
                continue
            await self._media_comments(media)

    async def _media_comments(self, media: InsightMedia) -> None:
        fetched: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(COMMENT_PAGES_PER_MEDIA):
            if self.result.calls >= self.settings.insights_max_calls_per_sync:
                raise BudgetSpent
            self.result.calls += 1
            page, after = await self.meta.list_comments(
                self.token, media.media_id, limit=50, after=after
            )
            fetched.extend(page)
            if after is None:
                break
        ids = [str(c["id"]) for c in fetched if c.get("id")]
        async with self.sf() as session, session.begin():
            known = set(
                (
                    await session.execute(
                        select(InsightComment.comment_id).where(InsightComment.comment_id.in_(ids))
                    )
                )
                .scalars()
                .all()
            )
            terms: Counter[str] = Counter()
            mentions = questions = added = 0
            for comment in fetched:
                cid = str(comment.get("id") or "")
                author = comment.get("from") or {}
                at = parse_timestamp(comment.get("timestamp"))
                if not cid or cid in known or at is None or str(author.get("id")) == self.owner_id:
                    continue
                text = str(comment.get("text") or "")
                terms.update(comment_terms(text))
                mentions += 1 if _MENTION.search(text) else 0
                questions += 1 if "?" in text else 0
                session.add(
                    InsightComment(
                        comment_id=cid,
                        media_id=media.media_id,
                        commented_at=at,
                        author_id=str(author["id"]) if author.get("id") else None,
                        username=str(author["username"])[:64] if author.get("username") else None,
                    )
                )
                known.add(cid)
                added += 1
            row = await session.get(InsightMedia, media.media_id)
            if row is not None:
                if added:
                    row.terms = merge_terms(row.terms or {}, terms)
                    metrics = dict(row.metrics or {})
                    metrics["comment_mentions"] = metrics.get("comment_mentions", 0) + mentions
                    metrics["comment_questions"] = metrics.get("comment_questions", 0) + questions
                    row.metrics = metrics
                if after is None:
                    row.comments_seen_count = row.comments_count
        self.result.comments_added += added


async def _put_snapshot(session: AsyncSession, key: str, data: Any, now: datetime) -> None:
    row = await session.get(InsightSnapshot, key)
    if row is None:
        session.add(InsightSnapshot(key=key, data=data, fetched_at=now))
    else:
        row.data = data
        row.fetched_at = now


async def _upsert_media(session: AsyncSession, item: dict[str, Any], now: datetime) -> None:
    posted = parse_timestamp(item.get("timestamp"))
    assert posted is not None  # noqa: S101 (filtered by the caller)
    row = await session.get(InsightMedia, str(item["id"]))
    if row is None:
        row = InsightMedia(media_id=str(item["id"]), metrics={}, terms={})
        session.add(row)
    row.posted_at = posted
    row.media_type = str(item.get("media_type") or "IMAGE")[:24]
    product = item.get("media_product_type")
    row.product_type = str(product)[:16] if product else None
    row.caption = item.get("caption")
    row.permalink = item.get("permalink")
    row.thumbnail_url = item.get("thumbnail_url")
    row.media_url = item.get("media_url")
    like_count, comments_count = (
        _number(item.get("like_count")),
        _number(item.get("comments_count")),
    )
    row.like_count = int(like_count) if like_count is not None else None
    row.comments_count = int(comments_count) if comments_count is not None else None
    row.listed_at = now


async def get_snapshot(session: AsyncSession, key: str) -> InsightSnapshot | None:
    return await session.get(InsightSnapshot, key)


async def sync_insights(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    meta: MetaClient,
    now: datetime | None = None,
) -> SyncResult:
    """One sync run. Never raises for Meta errors: they end up in the sync status."""
    now = now or utcnow()
    async with session_factory() as session:
        try:
            loaded = await load_token(session, settings)
        except TokenDecryptError:
            loaded = None
        unsupported_row = await session.get(InsightSnapshot, SNAPSHOT_UNSUPPORTED)
    if loaded is None or loaded[0].status != AccountStatus.ACTIVE:
        return SyncResult(errors=["no active account"])
    account, token = loaded
    unsupported = unsupported_row.data if unsupported_row and unsupported_row.data else {}
    run = _Run(session_factory, settings, meta, token, account.ig_user_id, now, unsupported)
    result = run.result

    async def step(name: str, coro: Any) -> bool:
        try:
            await coro
        except (BudgetSpent, MissingPermission):
            raise
        except MetaError as exc:
            result.errors.append(f"{name}: {exc}")
            if isinstance(exc, ConfirmedRejection) and exc.kind == RejectionKind.TOKEN_INVALID:
                raise BudgetSpent from None  # nothing else will work this run
            return False
        return True

    try:
        await step("profile", run.profile())
        await step("posts", run.list_media())
        try:
            await step("days", run.account_days(recent_only=True))
            await step("media insights", run.media_insights())
            await step("audience", run.audience())
        except MissingPermission:
            result.missing_permission = True
        await step("comments", run.comments())
        if not result.missing_permission:
            await step("days", run.account_days(recent_only=False))
    except BudgetSpent:
        result.partial = True
    except MissingPermission:
        result.missing_permission = True

    async with session_factory() as session, session.begin():
        await _put_snapshot(
            session,
            SNAPSHOT_UNSUPPORTED,
            {k: sorted(v) for k, v in run.unsupported.items()},
            now,
        )
        await _put_snapshot(
            session,
            SNAPSHOT_STATUS,
            {
                "finished_at": utcnow().isoformat(),
                "calls": result.calls,
                "partial": result.partial,
                "missing_permission": result.missing_permission,
                "errors": result.errors[:10],
            },
            now,
        )
    logger.info(
        "insights sync: %d calls, %d posts, %d refreshed, %d days, %d comments%s%s",
        result.calls,
        result.media_listed,
        result.media_refreshed,
        result.days_fetched,
        result.comments_added,
        " (partial)" if result.partial else "",
        " (no insights permission)" if result.missing_permission else "",
    )
    return result
