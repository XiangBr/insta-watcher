"""Competitor analysis API. Reads the stored tables; the buttons start work in the background."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.api_models import (
    CompetitorIn,
    CompetitorOut,
    CompetitorPostOut,
    CompetitorReportOut,
    CompetitorReportState,
    CompetitorScanOut,
    CompetitorsOut,
    CompetitorsStarted,
    CompetitorStats,
    Label,
    OptionStat,
    ReportContent,
    Taxonomy,
)
from app.apify import ApifyClient
from app.competitor_taxonomy import taxonomy_out
from app.competitors import (
    CAPTION_OUT_CHARS,
    POSTS_PER_PROFILE_OUT,
    CompetitorExists,
    CompetitorLimit,
    CompetitorView,
    add_competitor,
    classify_cost,
    generate_report,
    load_views,
    niche_from_views,
    normalize_username,
    pending_classification,
    remove_competitor,
    run_competitors,
)
from app.db import utcnow
from app.errors import ApiError
from app.models import CompetitorReport, CompetitorScan, RunStatus
from app.openrouter import OpenRouterClient
from app.routers.admin import require_admin
from app.routers.public import SessionFactoryDep, SettingsDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/competitors", dependencies=[Depends(require_admin)])


def _labels(raw: dict[str, Any] | None) -> dict[str, Label]:
    out: dict[str, Label] = {}
    for key, value in (raw or {}).items():
        if isinstance(value, dict) and isinstance(value.get("value"), str | int | float):
            out[key] = Label(value=value["value"], confidence=value.get("confidence"))
    return out


def _view_out(view: CompetitorView) -> CompetitorOut:
    c, s = view.comp, view.stats
    return CompetitorOut(
        id=c.id,
        username=c.username,
        is_self=c.is_self,
        full_name=c.full_name,
        biography=c.biography,
        category=c.category,
        external_url=c.external_url,
        profile_pic_url=c.profile_pic_url,
        followers=c.followers,
        follows=c.follows,
        posts_count=c.posts_count,
        verified=c.verified,
        private=c.private,
        scraped_at=c.scraped_at,
        scrape_error=c.scrape_error,
        classify_error=c.classify_error,
        labels=_labels(c.labels),
        stats=CompetitorStats(
            posts=s.posts,
            median_engagement_rate=s.median_engagement_rate,
            avg_likes=s.avg_likes,
            avg_comments=s.avg_comments,
            posts_per_week=s.posts_per_week,
            video_share=s.video_share,
        ),
        posts=[
            CompetitorPostOut(
                id=p.post_id,
                url=p.url,
                media_type=p.media_type,
                caption=p.caption[:CAPTION_OUT_CHARS] if p.caption else None,
                posted_at=p.posted_at,
                likes=p.likes,
                comments=p.comments,
                views=p.views,
                display_url=p.display_url,
                is_pinned=p.is_pinned,
                engagement_rate=rate,
                performance=perf,
                labels=_labels(p.labels),
            )
            for p, rate, perf in view.posts[:POSTS_PER_PROFILE_OUT]
        ],
    )


def _running(request: Request) -> bool:
    lock: asyncio.Lock | None = getattr(request.app.state, "competitors_lock", None)
    return bool(lock and lock.locked())


def _report_running(request: Request) -> bool:
    task: asyncio.Task[None] | None = getattr(request.app.state, "report_task", None)
    return task is not None and not task.done()


@router.get("")
async def competitors(
    request: Request, settings: SettingsDep, session_factory: SessionFactoryDep
) -> CompetitorsOut:
    """Everything the competitors page shows, from what is stored. Never calls Apify."""
    async with session_factory() as session:
        views = await load_views(session, utcnow())
        scan = await session.scalar(
            select(CompetitorScan).order_by(CompetitorScan.started_at.desc()).limit(1)
        )
        pending = await pending_classification(session)
        cost = await classify_cost(session)
    running_scan = scan is not None and scan.status == RunStatus.RUNNING
    return CompetitorsOut(
        collect_enabled=settings.apify_token is not None,
        classify_enabled=settings.openrouter_api_key is not None,
        running=_running(request) or running_scan,
        refresh_hours=settings.competitors_refresh_hours,
        max_competitors=settings.competitors_max,
        last_scan=CompetitorScanOut(
            status=scan.status,
            started_at=scan.started_at,
            finished_at=scan.finished_at,
            profiles=scan.profiles,
            posts=scan.posts,
            cost_usd=scan.cost_usd,
            error=scan.error,
        )
        if scan is not None
        else None,
        pending_classification=pending,
        classify_cost_usd=round(cost, 4),
        taxonomy=Taxonomy.model_validate(taxonomy_out()),
        competitors=[_view_out(v) for v in views],
        niche={
            key: [OptionStat.model_validate(r) for r in rows]
            for key, rows in niche_from_views(views).items()
        },
    )


@router.post("", status_code=201)
async def create(
    body: CompetitorIn, settings: SettingsDep, session_factory: SessionFactoryDep
) -> CompetitorsStarted:
    """Add a profile. It is collected on the next loop run (or with the update button)."""
    username = normalize_username(body.username)
    if username is None:
        raise ApiError(422, "not an Instagram username or profile URL")
    async with session_factory() as session, session.begin():
        try:
            await add_competitor(session, username, settings)
        except CompetitorExists:
            raise ApiError(409, "this profile is already in the list") from None
        except CompetitorLimit:
            raise ApiError(
                409, f"limit of {settings.competitors_max} profiles (COMPETITORS_MAX)"
            ) from None
    return CompetitorsStarted(started=True)


@router.delete("/{competitor_id}", status_code=204)
async def remove(competitor_id: int, session_factory: SessionFactoryDep) -> Response:
    async with session_factory() as session, session.begin():
        if not await remove_competitor(session, competitor_id):
            raise ApiError(404, "competitor not found")
    return Response(status_code=204)


@router.post("/scan", status_code=202)
async def scan_now(
    request: Request, settings: SettingsDep, session_factory: SessionFactoryDep
) -> CompetitorsStarted:
    """Collect every profile again now (billed by Apify), then classify what is new."""
    apify: ApifyClient | None = request.app.state.apify
    if apify is None:
        raise ApiError(409, "APIFY_TOKEN is not set")
    lock: asyncio.Lock = request.app.state.competitors_lock
    if lock.locked():
        return CompetitorsStarted(started=False)
    router_client: OpenRouterClient | None = request.app.state.openrouter

    async def run() -> None:
        async with lock:
            try:
                await run_competitors(session_factory, settings, apify, router_client, force=True)
            except Exception:
                logger.exception("competitor scan started from the dashboard failed")

    request.app.state.competitors_task = asyncio.create_task(run(), name="competitors-scan")
    await asyncio.sleep(0)
    return CompetitorsStarted(started=True)


def _report_out(row: CompetitorReport) -> CompetitorReportOut:
    return CompetitorReportOut(
        id=row.id,
        status=row.status,
        model=row.model,
        created_at=row.created_at,
        finished_at=row.finished_at,
        content=ReportContent.model_validate(row.content) if row.content else None,
        cost_usd=row.cost_usd,
        error=row.error,
    )


@router.get("/report")
async def latest_report(
    request: Request, settings: SettingsDep, session_factory: SessionFactoryDep
) -> CompetitorReportState:
    async with session_factory() as session:
        row = await session.scalar(
            select(CompetitorReport).order_by(CompetitorReport.created_at.desc()).limit(1)
        )
    return CompetitorReportState(
        enabled=settings.openrouter_api_key is not None,
        running=_report_running(request),
        report=_report_out(row) if row is not None else None,
    )


@router.post("/report", status_code=202)
async def start_report(
    request: Request, settings: SettingsDep, session_factory: SessionFactoryDep
) -> CompetitorsStarted:
    """Write a new report with COMPETITORS_REPORT_MODEL (billed by OpenRouter)."""
    router_client: OpenRouterClient | None = request.app.state.openrouter
    if router_client is None:
        raise ApiError(409, "OPENROUTER_API_KEY is not set")
    if _report_running(request):
        return CompetitorsStarted(started=False)
    async with session_factory() as session:
        views = await load_views(session, utcnow())
    if not any(v.comp.scraped_at and not v.comp.is_self for v in views):
        raise ApiError(409, "no competitor collected yet")
    async with session_factory() as session, session.begin():
        row = CompetitorReport(
            model=settings.competitors_report_model, status=RunStatus.RUNNING, created_at=utcnow()
        )
        session.add(row)
        await session.flush()
        report_id = row.id

    async def run() -> None:
        try:
            await generate_report(session_factory, settings, router_client, report_id)
        except Exception:
            logger.exception("competitor report failed")
            async with session_factory() as session, session.begin():
                failed = await session.get(CompetitorReport, report_id)
                if failed is not None and failed.status == RunStatus.RUNNING:
                    failed.status, failed.error = RunStatus.FAILED, "erro interno"
                    failed.finished_at = utcnow()

    request.app.state.report_task = asyncio.create_task(run(), name="competitors-report")
    return CompetitorsStarted(started=True)
