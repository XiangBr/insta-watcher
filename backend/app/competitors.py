"""Competitor analysis: collect public profiles with Apify, classify them with the decision
model, compare engagement, and write a report with a language model on demand.

Read only, like the insights sync: nothing here sends anything on Instagram. The loop runs every
COMPETITORS_INTERVAL_SECONDS and does at most one of each per run:

1. If an Apify run is going, check on it; when it finishes, store what it collected.
2. Otherwise, if a profile is due (never collected, or older than COMPETITORS_REFRESH_HOURS),
   start one run for every due profile.
3. Classify up to COMPETITORS_CLASSIFY_PER_RUN posts and the profiles not yet classified with
   the current TAXONOMY_VERSION.

Every Apify run and model call is billed to the owner's own key, so the costs reported by
Apify and OpenRouter are stored and shown.
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api_models import ReportContent
from app.apify import ApifyClient, ApifyError
from app.competitor_taxonomy import (
    POST_QUESTIONS,
    PROFILE_QUESTIONS,
    TAXONOMY_VERSION,
    Question,
)
from app.config import Settings
from app.db import utcnow
from app.models import (
    Account,
    Competitor,
    CompetitorPost,
    CompetitorReport,
    CompetitorScan,
    InsightSnapshot,
    RunStatus,
)
from app.openrouter import OpenRouterClient, OpenRouterError, parse_json_object

logger = logging.getLogger(__name__)

# Instagram usernames: letters, digits, dot and underscore, up to 30 characters.
_USERNAME = re.compile(r"^[a-z0-9._]{1,30}$")
_PROFILE_URL = re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/([^/?#\s]+)", re.IGNORECASE)
_RESERVED_PATHS = frozenset({"p", "reel", "reels", "explore", "stories", "tv", "accounts"})

# A run that has not finished after this long is given up on.
SCAN_TIMEOUT = timedelta(minutes=45)
POSTS_KEPT_PER_PROFILE = 50
POSTS_PER_PROFILE_OUT = 24
CAPTION_OUT_CHARS = 500
CAPTION_STATE_CHARS = 3000
PROFILE_CAPTIONS = 12
PROFILE_CAPTION_CHARS = 400
MIN_POSTS_FOR_NICHE = 3
DECISION_CONCURRENCY = 4
REPORTS_KEPT = 20
SNAPSHOT_STATUS = "competitors_status"


def normalize_username(raw: str) -> str | None:
    """'@User', 'user' or 'https://instagram.com/user/' -> 'user'. None if not a username."""
    value = raw.strip()
    if match := _PROFILE_URL.search(value):
        value = match.group(1)
        if value.lower() in _RESERVED_PATHS:
            return None
    value = value.lstrip("@").strip().strip("/").lower()
    return value if _USERNAME.match(value) else None


def _int(value: Any) -> int | None:
    """Apify reports -1 or nothing when a count is hidden."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return int(value)


def _str(value: Any, limit: int) -> str | None:
    return str(value)[:limit] if value not in (None, "") else None


def _https(value: Any) -> str | None:
    text = _str(value, 2048)
    return text if text and text.startswith("https://") else None


def parse_iso(value: Any) -> datetime | None:
    """Apify timestamps: 2026-10-01T12:00:00.000Z (ISO 8601, UTC)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


# ---- stats -------------------------------------------------------------------------------


def engagement_rate(post: CompetitorPost, followers: int | None) -> float | None:
    if not followers or post.likes is None:
        return None
    return (post.likes + (post.comments or 0)) / followers


@dataclass
class ProfileStats:
    posts: int
    median_engagement_rate: float | None
    avg_likes: float | None
    avg_comments: float | None
    posts_per_week: float | None
    video_share: float | None


def profile_stats(comp: Competitor, posts: list[CompetitorPost], now: datetime) -> ProfileStats:
    """Pinned posts are left out: they are old and chosen because they did well."""
    regular = [p for p in posts if not p.is_pinned]
    rates = [r for p in regular if (r := engagement_rate(p, comp.followers)) is not None]
    likes = [p.likes for p in regular if p.likes is not None]
    comments = [p.comments for p in regular if p.comments is not None]
    per_week: float | None = None
    if len(regular) >= 2:
        oldest = min(p.posted_at for p in regular)
        days = max((now - oldest).total_seconds() / 86400, 7.0)
        per_week = round(len(regular) / days * 7, 1)
    videos = sum(1 for p in regular if (p.media_type or "").lower() == "video")
    return ProfileStats(
        posts=len(regular),
        median_engagement_rate=median(rates) if rates else None,
        avg_likes=sum(likes) / len(likes) if likes else None,
        avg_comments=sum(comments) / len(comments) if comments else None,
        posts_per_week=per_week,
        video_share=videos / len(regular) if regular else None,
    )


def performance(rate: float | None, typical: float | None) -> float | None:
    """How a post did against its own profile's median. Comparable across profile sizes."""
    if rate is None or not typical:
        return None
    return rate / typical


def niche_breakdown(
    items: list[tuple[dict[str, Any], float]],
) -> dict[str, list[dict[str, Any]]]:
    """For each post question, the median performance of the posts in each option.

    `items` is (post labels, performance). Options with fewer than MIN_POSTS_FOR_NICHE posts
    are left out: one viral post says nothing about a format.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for key, question in POST_QUESTIONS.items():
        if question.type != "choice":
            continue
        groups: dict[str, list[float]] = defaultdict(list)
        for labels, perf in items:
            label = labels.get(key)
            if isinstance(label, dict) and isinstance(label.get("value"), str):
                groups[label["value"]].append(perf)
        rows = [
            {"option": option, "posts": len(values), "median_performance": round(median(values), 3)}
            for option, values in groups.items()
            if len(values) >= MIN_POSTS_FOR_NICHE
        ]
        out[key] = sorted(rows, key=lambda r: r["median_performance"], reverse=True)
    return out


# ---- competitors list --------------------------------------------------------------------


class CompetitorLimit(Exception):
    pass


class CompetitorExists(Exception):
    pass


async def add_competitor(session: AsyncSession, username: str, settings: Settings) -> Competitor:
    existing = await session.scalar(select(Competitor).where(Competitor.username == username))
    if existing is not None:
        raise CompetitorExists
    count = await session.scalar(
        select(func.count()).select_from(Competitor).where(Competitor.is_self.is_(False))
    )
    if (count or 0) >= settings.competitors_max:
        raise CompetitorLimit
    comp = Competitor(username=username, is_self=False, labels={})
    session.add(comp)
    await session.flush()
    return comp


async def remove_competitor(session: AsyncSession, competitor_id: int) -> bool:
    comp = await session.get(Competitor, competitor_id)
    if comp is None:
        return False
    await session.execute(delete(CompetitorPost).where(CompetitorPost.competitor_id == comp.id))
    await session.delete(comp)
    return True


async def ensure_self(session: AsyncSession) -> None:
    """Keep the owner's account in the list, collected like the others."""
    account = await session.get(Account, 1)
    if account is None or not account.username:
        return
    username = account.username.lower()
    current = await session.scalar(select(Competitor).where(Competitor.is_self.is_(True)))
    if current is not None and current.username == username:
        return
    if current is not None:
        await remove_competitor(session, current.id)
    same = await session.scalar(select(Competitor).where(Competitor.username == username))
    if same is not None:
        same.is_self = True
    else:
        session.add(Competitor(username=username, is_self=True, labels={}))


# ---- collection --------------------------------------------------------------------------


async def _status(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(InsightSnapshot, SNAPSHOT_STATUS)
    return dict(row.data) if row is not None and isinstance(row.data, dict) else {}


async def _add_cost(session: AsyncSession, cost: float, now: datetime) -> None:
    row = await session.get(InsightSnapshot, SNAPSHOT_STATUS)
    data = dict(row.data) if row is not None and isinstance(row.data, dict) else {}
    data["classify_cost_usd"] = float(data.get("classify_cost_usd") or 0) + cost
    if row is None:
        session.add(InsightSnapshot(key=SNAPSHOT_STATUS, data=data, fetched_at=now))
    else:
        row.data = data
        row.fetched_at = now


def _apply_profile(comp: Competitor, item: dict[str, Any], now: datetime) -> None:
    comp.full_name = _str(item.get("fullName"), 256)
    comp.biography = _str(item.get("biography"), 4000)
    comp.category = _str(item.get("businessCategoryName"), 128)
    comp.external_url = _https(item.get("externalUrl"))
    comp.profile_pic_url = _https(item.get("profilePicUrlHD") or item.get("profilePicUrl"))
    comp.followers = _int(item.get("followersCount"))
    comp.follows = _int(item.get("followsCount"))
    comp.posts_count = _int(item.get("postsCount"))
    comp.verified = bool(item.get("verified"))
    comp.private = bool(item.get("private"))
    comp.scraped_at = now
    comp.scrape_error = "perfil privado" if comp.private else None
    # The profile labels read the latest captions, which just changed.
    comp.labels_version = None


async def _apply_posts(
    session: AsyncSession, comp: Competitor, items: list[Any], now: datetime
) -> int:
    stored = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        posted = parse_iso(item.get("timestamp"))
        if posted is None:
            continue
        post = await session.get(CompetitorPost, str(item["id"])[:64])
        if post is None:
            post = CompetitorPost(
                post_id=str(item["id"])[:64], competitor_id=comp.id, labels={}, hashtags=[]
            )
            session.add(post)
        caption = _str(item.get("caption"), 8000)
        if caption != post.caption:
            post.labels_version = None  # edited caption: classify again
        post.competitor_id = comp.id
        post.short_code = _str(item.get("shortCode"), 32)
        post.url = _https(item.get("url"))
        post.media_type = _str(item.get("type"), 16)
        post.product_type = _str(item.get("productType"), 16)
        post.caption = caption
        tags = item.get("hashtags")
        post.hashtags = [str(t)[:100] for t in tags[:30]] if isinstance(tags, list) else []
        post.posted_at = posted
        post.likes = _int(item.get("likesCount"))
        post.comments = _int(item.get("commentsCount"))
        post.views = _int(item.get("videoViewCount") or item.get("videoPlayCount"))
        post.display_url = _https(item.get("displayUrl"))
        post.is_pinned = bool(item.get("isPinned"))
        post.scraped_at = now
        stored += 1
    await session.flush()
    # Keep the newest posts only.
    old = (
        select(CompetitorPost.post_id)
        .where(CompetitorPost.competitor_id == comp.id)
        .order_by(CompetitorPost.posted_at.desc())
        .offset(POSTS_KEPT_PER_PROFILE)
    )
    await session.execute(delete(CompetitorPost).where(CompetitorPost.post_id.in_(old)))
    return stored


async def ingest(
    session: AsyncSession, scan: CompetitorScan, items: list[dict[str, Any]], now: datetime
) -> None:
    """Store what one run collected. Profiles asked for but missing get an error."""
    by_name: dict[str, dict[str, Any]] = {}
    for item in items:
        name = str(item.get("username") or "").lower()
        if name and not item.get("error"):
            by_name[name] = item
    profiles = posts = 0
    for username in scan.usernames:
        comp = await session.scalar(select(Competitor).where(Competitor.username == username))
        if comp is None:
            continue  # removed while the run was going
        found = by_name.get(username)
        if found is None:
            comp.scraped_at = now
            comp.scrape_error = "perfil não encontrado ou indisponível"
            continue
        _apply_profile(comp, found, now)
        latest = found.get("latestPosts")
        posts += await _apply_posts(session, comp, latest if isinstance(latest, list) else [], now)
        profiles += 1
    scan.profiles, scan.posts = profiles, posts


async def _due_usernames(session: AsyncSession, settings: Settings, now: datetime) -> list[str]:
    stale = now - timedelta(hours=settings.competitors_refresh_hours)
    rows = await session.scalars(
        select(Competitor.username)
        .where(or_(Competitor.scraped_at.is_(None), Competitor.scraped_at < stale))
        .order_by(Competitor.is_self.desc(), Competitor.username)
    )
    return list(rows)


async def scan_step(
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    apify: ApifyClient,
    now: datetime,
    *,
    force: bool = False,
) -> None:
    """Check on the running Apify run, or start one if any profile is due (all, if forced)."""
    async with sf() as session, session.begin():
        await ensure_self(session)
        running = await session.scalar(
            select(CompetitorScan).where(CompetitorScan.status == RunStatus.RUNNING)
        )
    if running is not None:
        await _check_scan(sf, apify, running.id, now)
        return
    async with sf() as session:
        if force:
            usernames = list(await session.scalars(select(Competitor.username)))
        else:
            usernames = await _due_usernames(session, settings, now)
    if not usernames:
        return
    scan = CompetitorScan(usernames=usernames, started_at=now, status=RunStatus.RUNNING)
    try:
        run = await apify.start_profiles(settings.apify_profile_actor, usernames)
    except ApifyError as exc:
        scan.status, scan.error, scan.finished_at = RunStatus.FAILED, str(exc), now
        logger.warning("competitors: could not start the Apify run: %s", exc)
    else:
        scan.apify_run_id, scan.dataset_id = run.id, run.dataset_id
        logger.info("competitors: Apify run started for %d profiles", len(usernames))
    async with sf() as session, session.begin():
        session.add(scan)


async def _check_scan(
    sf: async_sessionmaker[AsyncSession], apify: ApifyClient, scan_id: int, now: datetime
) -> None:
    async with sf() as session:
        scan = await session.get(CompetitorScan, scan_id)
    if scan is None or scan.apify_run_id is None:
        return
    error: str | None = None
    items: list[dict[str, Any]] | None = None
    cost: float | None = None
    try:
        run = await apify.get_run(scan.apify_run_id)
        cost = run.cost_usd
        if not run.finished:
            if now - scan.started_at < SCAN_TIMEOUT:
                return
            error = "a coleta passou do tempo limite"
        elif not run.succeeded:
            error = f"a coleta terminou com {run.status}"
        else:
            items = await apify.dataset_items(run.dataset_id or scan.dataset_id or "")
    except ApifyError as exc:
        if now - scan.started_at < SCAN_TIMEOUT:
            logger.warning("competitors: checking the Apify run failed: %s", exc)
            return
        error = str(exc)
    async with sf() as session, session.begin():
        row = await session.get(CompetitorScan, scan_id)
        if row is None:
            return
        row.cost_usd = cost
        row.finished_at = now
        if items is None:
            row.status, row.error = RunStatus.FAILED, error
        else:
            await ingest(session, row, items, now)
            row.status = RunStatus.SUCCEEDED
    logger.info("competitors: Apify run finished (%s)", "ok" if items is not None else "failed")


# ---- classification ----------------------------------------------------------------------


def labels_from_answers(
    questions: dict[str, Question], answers: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Keep the answer and its confidence, only for questions we asked and valid values."""
    out: dict[str, Any] = {}
    for key, question in questions.items():
        answer = answers.get(key) or {}
        confidence = answer.get("confidence")
        conf = float(confidence) if isinstance(confidence, int | float) else None
        if question.type == "choice":
            value = answer.get("choice")
            if isinstance(value, str) and value in question.options:
                out[key] = {"value": value, "confidence": conf}
        elif question.type == "score":
            value = answer.get("score")
            if isinstance(value, int | float) and not isinstance(value, bool):
                out[key] = {"value": round(float(value), 3), "confidence": conf}
        else:
            value = answer.get("noul")
            if isinstance(value, int | float) and not isinstance(value, bool):
                out[key] = {"value": round(float(value), 3), "confidence": None}
    return out


def post_state(post: CompetitorPost, username: str) -> dict[str, Any]:
    return {
        "perfil": f"@{username}",
        "tipo_de_midia": post.media_type or "desconhecido",
        "legenda": (post.caption or "")[:CAPTION_STATE_CHARS],
        "hashtags": post.hashtags[:30],
    }


def profile_state(comp: Competitor, captions: list[str]) -> dict[str, Any]:
    return {
        "perfil": f"@{comp.username}",
        "nome": comp.full_name or "",
        "categoria": comp.category or "",
        "bio": comp.biography or "",
        "link_na_bio": comp.external_url or "",
        "legendas_recentes": [c[:PROFILE_CAPTION_CHARS] for c in captions],
    }


def _decision_questions(questions: dict[str, Question]) -> dict[str, dict[str, Any]]:
    return {key: q.as_decision() for key, q in questions.items()}


async def classify_step(
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    router: OpenRouterClient,
    now: datetime,
) -> int:
    """Classify what is not classified with the current taxonomy. Returns posts classified."""
    async with sf() as session:
        profiles = list(
            await session.scalars(
                select(Competitor).where(
                    Competitor.scraped_at.is_not(None),
                    Competitor.scrape_error.is_(None),
                    or_(
                        Competitor.labels_version.is_(None),
                        Competitor.labels_version != TAXONOMY_VERSION,
                    ),
                )
            )
        )
        posts = list(
            (
                await session.execute(
                    select(CompetitorPost, Competitor.username)
                    .join(Competitor, Competitor.id == CompetitorPost.competitor_id)
                    .where(
                        or_(
                            CompetitorPost.labels_version.is_(None),
                            CompetitorPost.labels_version != TAXONOMY_VERSION,
                        )
                    )
                    .order_by(CompetitorPost.posted_at.desc())
                    .limit(settings.competitors_classify_per_run)
                )
            ).all()
        )
        captions: dict[int, list[str]] = {}
        for comp in profiles:
            captions[comp.id] = [
                c
                for c in await session.scalars(
                    select(CompetitorPost.caption)
                    .where(
                        CompetitorPost.competitor_id == comp.id,
                        CompetitorPost.caption.is_not(None),
                    )
                    .order_by(CompetitorPost.posted_at.desc())
                    .limit(PROFILE_CAPTIONS)
                )
                if c
            ]

    gate = asyncio.Semaphore(DECISION_CONCURRENCY)
    post_questions = _decision_questions(POST_QUESTIONS)
    profile_questions = _decision_questions(PROFILE_QUESTIONS)

    async def decide(state: dict[str, Any], questions: dict[str, Any]) -> Any:
        async with gate:
            try:
                return await router.decide(settings.jev_model, state, questions)
            except OpenRouterError as exc:
                return exc

    post_jobs = [
        decide(post_state(post, username), post_questions)
        for post, username in posts
        if (post.caption or "").strip()
    ]
    profile_jobs = [decide(profile_state(c, captions[c.id]), profile_questions) for c in profiles]
    results = await asyncio.gather(*post_jobs, *profile_jobs)
    post_results = iter(results[: len(post_jobs)])
    profile_results = iter(results[len(post_jobs) :])

    cost = 0.0
    done = 0
    async with sf() as session, session.begin():
        for post, _ in posts:
            row = await session.get(CompetitorPost, post.post_id)
            if row is None:
                continue
            if not (post.caption or "").strip():
                # Nothing to read: no labels, and not asked again until the caption changes.
                row.labels, row.labels_version, row.classify_error = {}, TAXONOMY_VERSION, None
                continue
            result = next(post_results)
            if isinstance(result, OpenRouterError):
                row.classify_error = str(result)
                continue
            row.labels = labels_from_answers(POST_QUESTIONS, result.answers)
            row.labels_version, row.classify_error = TAXONOMY_VERSION, None
            cost += result.cost_usd or 0
            done += 1
        for comp in profiles:
            result = next(profile_results)
            row_c = await session.get(Competitor, comp.id)
            if row_c is None:
                continue
            if isinstance(result, OpenRouterError):
                row_c.classify_error = str(result)
                continue
            row_c.labels = labels_from_answers(PROFILE_QUESTIONS, result.answers)
            row_c.labels_version, row_c.classify_error = TAXONOMY_VERSION, None
            cost += result.cost_usd or 0
        if cost:
            await _add_cost(session, cost, now)
    if done or profiles:
        logger.info("competitors: classified %d posts and %d profiles", done, len(profiles))
    return done


async def run_competitors(
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    apify: ApifyClient | None,
    router: OpenRouterClient | None,
    now: datetime | None = None,
    *,
    force: bool = False,
) -> None:
    """One loop run. External errors end up in the stored rows, never raised."""
    now = now or utcnow()
    if apify is not None:
        await scan_step(sf, settings, apify, now, force=force)
    if router is not None:
        await classify_step(sf, settings, router, now)


async def pending_classification(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(CompetitorPost)
        .where(
            or_(
                CompetitorPost.labels_version.is_(None),
                CompetitorPost.labels_version != TAXONOMY_VERSION,
            )
        )
    )
    return int(count or 0)


async def classify_cost(session: AsyncSession) -> float:
    return float((await _status(session)).get("classify_cost_usd") or 0)


# ---- the view ----------------------------------------------------------------------------


@dataclass
class CompetitorView:
    comp: Competitor
    stats: ProfileStats
    posts: list[tuple[CompetitorPost, float | None, float | None]]  # post, rate, performance


async def load_views(session: AsyncSession, now: datetime) -> list[CompetitorView]:
    comps = list(
        await session.scalars(
            select(Competitor).order_by(Competitor.is_self.desc(), Competitor.followers.desc())
        )
    )
    posts_by: dict[int, list[CompetitorPost]] = defaultdict(list)
    for post in await session.scalars(
        select(CompetitorPost).order_by(CompetitorPost.posted_at.desc())
    ):
        posts_by[post.competitor_id].append(post)
    views = []
    for comp in comps:
        posts = posts_by.get(comp.id, [])
        stats = profile_stats(comp, posts, now)
        rows = []
        for post in posts:
            rate = engagement_rate(post, comp.followers)
            rows.append((post, rate, performance(rate, stats.median_engagement_rate)))
        views.append(CompetitorView(comp, stats, rows))
    return views


def niche_from_views(views: list[CompetitorView]) -> dict[str, list[dict[str, Any]]]:
    items = [
        (post.labels or {}, perf)
        for view in views
        if not view.comp.is_self
        for post, _, perf in view.posts
        if perf is not None and not post.is_pinned
    ]
    return niche_breakdown(items)


# ---- report ------------------------------------------------------------------------------

REPORT_SYSTEM = """Você é estrategista de conteúdo para Instagram. Recebe dados públicos de \
perfis concorrentes e da conta do usuário (quando houver), já classificados por um modelo de \
decisão, e escreve uma análise prática e específica, em português do Brasil.

Regras:
- Baseie cada afirmação nos dados recebidos. Cite @perfis e números quando ajudar.
- "desempenho" de um post é o engajamento dele dividido pela mediana do próprio perfil: 2.0 \
significa o dobro do normal daquele perfil.
- Em "tecnologias", liste ferramentas, produtos e tecnologias citados nas legendas e na bio.
- As ideias devem ser posts que o usuário pode gravar esta semana. Cada ideia traz uma \
palavra-chave curta (uma palavra, sem acento) para uma campanha de "comente X que eu te mando".
- Responda só com um objeto JSON neste formato:
{"summary": str, "what_works": [str], "gaps": [str],
 "competitors": [{"username": str, "summary": str, "strength": str, "technologies": [str]}],
 "ideas": [{"title": str, "format": str, "hook": str, "keyword": str, "why": str}]}
"""


REPORT_RETRY = """Sua resposta anterior não pôde ser lida ({problem}). Responda de novo só com \
o objeto JSON, no formato pedido, sem texto antes ou depois e sem bloco de código. Seja mais \
conciso: no máximo 6 itens por lista e 5 ideias."""
REPORT_ATTEMPTS = 2
REPORT_SCHEMA = ReportContent.model_json_schema()


def _label_text(question: Question, label: Any) -> Any:
    if not isinstance(label, dict):
        return None
    value = label.get("value")
    if question.type == "choice" and isinstance(value, str) and value in question.options:
        return question.options[value].label
    if question.type == "score" and isinstance(value, int | float):
        levels = list(question.options.values())
        return levels[min(round(value), len(levels) - 1)].label
    if question.type == "noul" and isinstance(value, int | float):
        return value >= 0.5
    return None


def _labels_text(questions: dict[str, Question], labels: dict[str, Any]) -> dict[str, Any]:
    return {
        q.label: text
        for key, q in questions.items()
        if (text := _label_text(q, labels.get(key))) is not None
    }


def report_input(views: list[CompetitorView]) -> dict[str, Any]:
    def describe(view: CompetitorView) -> dict[str, Any]:
        ranked = sorted(
            (row for row in view.posts if row[2] is not None and not row[0].is_pinned),
            key=lambda row: row[2] or 0,
            reverse=True,
        )

        def post(row: tuple[CompetitorPost, float | None, float | None]) -> dict[str, Any]:
            p, _, perf = row
            return {
                "legenda": (p.caption or "")[:300],
                "tipo": p.media_type,
                "desempenho": round(perf or 0, 2),
                "rotulos": _labels_text(POST_QUESTIONS, p.labels or {}),
            }

        s = view.stats
        return {
            "perfil": f"@{view.comp.username}",
            "seguidores": view.comp.followers,
            "categoria": view.comp.category,
            "bio": view.comp.biography,
            "rotulos": _labels_text(PROFILE_QUESTIONS, view.comp.labels or {}),
            "engajamento_mediano_pct": round(s.median_engagement_rate * 100, 2)
            if s.median_engagement_rate is not None
            else None,
            "posts_por_semana": s.posts_per_week,
            "parcela_de_video": round(s.video_share, 2) if s.video_share is not None else None,
            "melhores_posts": [post(r) for r in ranked[:5]],
            "piores_posts": [post(r) for r in ranked[-3:]] if len(ranked) > 8 else [],
        }

    collected = [v for v in views if v.comp.scraped_at is not None and not v.comp.private]
    me = next((v for v in collected if v.comp.is_self), None)
    niche = niche_from_views(collected)
    return {
        "minha_conta": describe(me) if me else None,
        "concorrentes": [describe(v) for v in collected if not v.comp.is_self],
        "o_que_performa_no_nicho": {
            POST_QUESTIONS[key].label: [
                {
                    "opcao": POST_QUESTIONS[key].options[row["option"]].label,
                    "desempenho_mediano": row["median_performance"],
                    "posts": row["posts"],
                }
                for row in rows
            ]
            for key, rows in niche.items()
            if rows
        },
    }


async def generate_report(
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    router: OpenRouterClient,
    report_id: int,
) -> None:
    """Fill one RUNNING report. Never raises for model errors: they end up in the row."""
    async with sf() as session:
        views = await load_views(session, utcnow())
    messages = [
        {"role": "system", "content": REPORT_SYSTEM},
        {"role": "user", "content": json.dumps(report_input(views), ensure_ascii=False)},
    ]
    content: dict[str, Any] | None = None
    cost: float | None = None
    error: str | None = None
    # A model that answers with broken or cut-off JSON gets one chance to fix it.
    for attempt in range(REPORT_ATTEMPTS):
        try:
            completion = await router.complete_json(
                settings.competitors_report_model, messages, REPORT_SCHEMA
            )
        except OpenRouterError as exc:
            error = str(exc)
            break
        if completion.cost_usd is not None:
            cost = (cost or 0) + completion.cost_usd
        problem: str
        try:
            content = ReportContent.model_validate(parse_json_object(completion.text)).model_dump()
            error = None
            break
        except OpenRouterError as exc:
            problem = str(exc)
        except ValidationError:
            problem = "model answer does not follow the requested format"
        if completion.truncated:
            problem = "the answer hit the token limit and was cut off"
        error = problem
        logger.warning("competitors: report %d attempt %d: %s", report_id, attempt + 1, problem)
        messages = [
            *messages,
            {"role": "assistant", "content": completion.text},
            {"role": "user", "content": REPORT_RETRY.format(problem=problem)},
        ]
    async with sf() as session, session.begin():
        row = await session.get(CompetitorReport, report_id)
        if row is not None:
            row.status = RunStatus.SUCCEEDED if content is not None else RunStatus.FAILED
            row.content, row.cost_usd, row.error = content, cost, error
            row.finished_at = utcnow()
        old = (
            select(CompetitorReport.id)
            .order_by(CompetitorReport.created_at.desc())
            .offset(REPORTS_KEPT)
        )
        await session.execute(delete(CompetitorReport).where(CompetitorReport.id.in_(old)))
    logger.info("competitors: report %d %s", report_id, "done" if content else "failed")


async def fail_stale_reports(sf: async_sessionmaker[AsyncSession]) -> None:
    """One process: a report RUNNING at boot was cut off by the restart."""
    async with sf() as session, session.begin():
        for row in await session.scalars(
            select(CompetitorReport).where(CompetitorReport.status == RunStatus.RUNNING)
        ):
            row.status, row.error, row.finished_at = (
                RunStatus.FAILED,
                "interrompido por um reinício",
                utcnow(),
            )
