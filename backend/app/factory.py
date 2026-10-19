"""App factory. Kept apart from main.py so tests can build the app without the environment."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.apify import ApifyClient
from app.chat_worker import ChatWorker
from app.chat_worker import recover_stale as recover_chat_outbox
from app.competitors import fail_stale_reports, run_competitors
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.dispatcher import dispatch_pending
from app.errors import install_error_handlers
from app.insights import sync_insights
from app.logging_setup import install_redaction
from app.loops import LoopGroup, run_periodic
from app.maintenance import run_maintenance
from app.matching import KeywordMatcher
from app.meta.client import MetaClient
from app.openrouter import OpenRouterClient
from app.queue import recover_stale
from app.ratelimit import RateLimiter
from app.reconciler import reconcile
from app.routers import admin, chat, competitors, insights, legal, public
from app.worker import Worker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    install_redaction()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        session_factory = create_session_factory(engine)
        http = httpx.AsyncClient(follow_redirects=False)
        meta = MetaClient(http, settings.meta_graph_api_version)
        limiter = RateLimiter(session_factory, settings.private_reply_hourly_limit)
        dispatch_wake, worker_wake = asyncio.Event(), asyncio.Event()

        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.meta = meta
        app.state.limiter = limiter
        app.state.dispatch_wake = dispatch_wake
        app.state.worker_wake = worker_wake
        chat_wake = asyncio.Event()
        app.state.chat_wake = chat_wake
        # The admin API can trigger a sweep; it must never overlap with the loop's.
        reconcile_lock = asyncio.Lock()
        app.state.reconcile_lock = reconcile_lock
        # Same idea for the insights sync: the button and the loop never run it twice at once.
        insights_lock = asyncio.Lock()
        app.state.insights_lock = insights_lock
        app.state.insights_task = None
        # Competitor analysis: each client exists only when its key is set.
        apify = (
            ApifyClient(http, settings.apify_token.get_secret_value())
            if settings.apify_token
            else None
        )
        openrouter = (
            OpenRouterClient(http, settings.openrouter_api_key.get_secret_value())
            if settings.openrouter_api_key
            else None
        )
        app.state.apify = apify
        app.state.openrouter = openrouter
        competitors_lock = asyncio.Lock()
        app.state.competitors_lock = competitors_lock
        app.state.competitors_task = None
        app.state.report_task = None

        loops = LoopGroup()
        if settings.background_tasks_enabled:
            # One process: every RUNNING job left over from the last run is stale.
            await recover_stale(session_factory, at_boot=True)
            await recover_chat_outbox(session_factory, at_boot=True)
            matcher = KeywordMatcher()
            worker = Worker(session_factory, settings, meta, limiter)

            async def dispatcher_step() -> None:
                ai_available = settings.openrouter_api_key is not None
                if await dispatch_pending(session_factory, matcher, ai_available=ai_available):
                    worker_wake.set()
                    chat_wake.set()

            async def worker_step() -> None:
                await worker.run_until_idle()

            async def reconciler_step() -> None:
                async with reconcile_lock:
                    queued = await reconcile(session_factory, settings, meta, matcher)
                if queued:
                    worker_wake.set()

            async def maintenance_step() -> None:
                await run_maintenance(session_factory, settings, meta)

            loops.start(
                "dispatcher",
                run_periodic(
                    "dispatcher",
                    settings.dispatcher_interval_seconds,
                    dispatcher_step,
                    wake=dispatch_wake,
                ),
            )
            loops.start(
                "worker",
                run_periodic(
                    "worker", settings.worker_interval_seconds, worker_step, wake=worker_wake
                ),
            )
            chat_worker = ChatWorker(session_factory, settings, meta, openrouter)

            async def chat_step() -> None:
                await chat_worker.run_until_idle()

            loops.start(
                "chat",
                run_periodic("chat", settings.chat_interval_seconds, chat_step, wake=chat_wake),
            )
            loops.start(
                "reconciler",
                run_periodic("reconciler", settings.reconcile_interval_seconds, reconciler_step),
            )

            async def insights_step() -> None:
                if insights_lock.locked():
                    return
                async with insights_lock:
                    await sync_insights(session_factory, settings, meta)

            loops.start(
                "insights",
                run_periodic("insights", settings.insights_sync_interval_seconds, insights_step),
            )
            await fail_stale_reports(session_factory)
            if apify is not None or openrouter is not None:

                async def competitors_step() -> None:
                    if competitors_lock.locked():
                        return
                    async with competitors_lock:
                        await run_competitors(session_factory, settings, apify, openrouter)

                loops.start(
                    "competitors",
                    run_periodic(
                        "competitors", settings.competitors_interval_seconds, competitors_step
                    ),
                )
            loops.start(
                "maintenance",
                run_periodic(
                    "maintenance", settings.maintenance_interval_seconds, maintenance_step
                ),
            )
        try:
            yield
        finally:
            await loops.stop()
            for name in ("insights_task", "competitors_task", "report_task"):
                task = getattr(app.state, name)
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await http.aclose()
            await engine.dispose()

    docs = settings.enable_api_docs
    app = FastAPI(
        title="insta-watcher",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(public.router)
    app.include_router(legal.router)
    app.include_router(admin.router)
    app.include_router(insights.router)
    app.include_router(competitors.router)
    app.include_router(chat.router)
    return app
