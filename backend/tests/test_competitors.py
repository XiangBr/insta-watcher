import json
import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.apify import APIFY_HOST, ApifyClient
from app.competitor_taxonomy import POST_QUESTIONS, PROFILE_QUESTIONS, TAXONOMY_VERSION
from app.competitors import (
    SCAN_TIMEOUT,
    classify_cost,
    ensure_self,
    generate_report,
    labels_from_answers,
    niche_breakdown,
    normalize_username,
    profile_stats,
    run_competitors,
)
from app.config import Settings
from app.db import utcnow
from app.models import (
    Account,
    Competitor,
    CompetitorPost,
    CompetitorReport,
    CompetitorScan,
    RunStatus,
)
from app.openrouter import OPENROUTER_HOST, OpenRouterClient, OpenRouterError, parse_json_object
from tests.conftest import strong_secrets

APIFY_TOKEN = "apify_api_" + secrets.token_hex(16)
OPENROUTER_KEY = "sk-or-v1-" + secrets.token_hex(32)
RUNS = f"{APIFY_HOST}/v2/acts/apify~instagram-profile-scraper/runs"
DECISIONS = f"{OPENROUTER_HOST}/api/alpha/decisions"
CHAT = f"{OPENROUTER_HOST}/api/v1/chat/completions"


def stamp(days_ago: float) -> str:
    return (utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def apify_profile(username: str, followers: int, likes: list[int]) -> dict[str, Any]:
    return {
        "username": username,
        "fullName": username.title(),
        "biography": "Automação e I.A. para criadores",
        "businessCategoryName": "Educação",
        "externalUrl": "https://exemplo.com",
        "profilePicUrl": "https://scontent.cdninstagram.com/p.jpg",
        "followersCount": followers,
        "followsCount": 10,
        "postsCount": 100,
        "verified": False,
        "private": False,
        "latestPosts": [
            {
                "id": f"{username}_{i}",
                "shortCode": f"sc{i}",
                "url": f"https://www.instagram.com/p/sc{i}/",
                "type": "Video" if i % 2 else "Image",
                "caption": f"Comenta AUTOMACAO que eu te mando o passo a passo {i}",
                "hashtags": ["ia"],
                "timestamp": stamp(i + 1),
                "likesCount": n,
                "commentsCount": 5,
                "displayUrl": "https://scontent.cdninstagram.com/d.jpg",
            }
            for i, n in enumerate(likes)
        ],
    }


def post_answers() -> dict[str, Any]:
    return {
        "answers": {
            "format": {"type": "choice", "choice": "tutorial", "confidence": 0.9},
            "hook": {"type": "choice", "choice": "promise", "confidence": 0.7},
            "cta": {"type": "choice", "choice": "comment_keyword", "confidence": 0.95},
            "tone": {"type": "choice", "choice": "educational", "confidence": 0.8},
            "topic": {"type": "choice", "choice": "automation", "confidence": 0.8},
            "level": {"type": "score", "score": 0.4, "confidence": 0.6},
            # Profile questions, answered when the state is a profile.
            "audience": {"type": "choice", "choice": "creators", "confidence": 0.8},
            "positioning": {"type": "choice", "choice": "teacher", "confidence": 0.8},
            "monetization": {"type": "choice", "choice": "course", "confidence": 0.6},
            "comment_to_dm": {"type": "noul", "noul": 0.93},
            "depth": {"type": "score", "score": 1.2, "confidence": 0.5},
        },
        "usage": {"input_tokens": 300, "output_tokens": 0, "cost": 0.0002},
    }


@pytest.fixture
def keyed_settings(database_url: str) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=database_url,
        apify_token=APIFY_TOKEN,
        openrouter_api_key=OPENROUTER_KEY,
        **strong_secrets(),
    )


@pytest.fixture
async def keyed_app(keyed_settings: Settings) -> AsyncIterator[FastAPI]:
    from app.factory import create_app

    application = create_app(keyed_settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def keyed_api(
    keyed_app: FastAPI, keyed_settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=keyed_app)
    token = keyed_settings.api_admin_token.get_secret_value()
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"authorization": f"Bearer {token}"}
    ) as c:
        yield c


def factory_of(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    sf: async_sessionmaker[AsyncSession] = app.state.session_factory
    return sf


# ---- pure helpers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@Fulano.Dev", "fulano.dev"),
        ("fulano_dev", "fulano_dev"),
        ("https://www.instagram.com/fulano/", "fulano"),
        ("instagram.com/fulano?igsh=abc", "fulano"),
        ("https://www.instagram.com/p/C1234/", None),
        ("nome com espaço", None),
        ("a" * 31, None),
        ("", None),
    ],
)
def test_normalize_username(raw: str, expected: str | None) -> None:
    assert normalize_username(raw) == expected


def test_empty_keys_mean_off_and_bad_keys_are_refused(database_url: str) -> None:
    off = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=database_url,
        apify_token="",
        openrouter_api_key="  ",
        **strong_secrets(),
    )
    assert off.apify_token is None and off.openrouter_api_key is None
    with pytest.raises(ValidationError) as caught:
        Settings(  # type: ignore[call-arg]
            _env_file=None, database_url=database_url, apify_token="short", **strong_secrets()
        )
    assert "short" not in str(caught.value.errors(include_input=False))


def test_labels_keep_only_known_options_and_numbers() -> None:
    labels = labels_from_answers(
        POST_QUESTIONS,
        {
            "format": {"choice": "invented", "confidence": 0.9},
            "hook": {"choice": "question", "confidence": 0.8},
            "level": {"score": 1.234567, "confidence": 0.5},
        },
    )
    assert "format" not in labels
    assert labels["hook"] == {"value": "question", "confidence": 0.8}
    assert labels["level"]["value"] == 1.235
    noul = labels_from_answers(PROFILE_QUESTIONS, {"comment_to_dm": {"noul": 0.81}})
    assert noul["comment_to_dm"] == {"value": 0.81, "confidence": None}


def test_profile_stats_leave_pinned_posts_out() -> None:
    now = utcnow()
    comp = Competitor(username="x", followers=1000)
    posts = [
        CompetitorPost(likes=95, comments=5, posted_at=now - timedelta(days=1), is_pinned=False),
        CompetitorPost(likes=45, comments=5, posted_at=now - timedelta(days=14), is_pinned=False),
        CompetitorPost(likes=9000, comments=0, posted_at=now - timedelta(days=300), is_pinned=True),
    ]
    stats = profile_stats(comp, posts, now)
    assert stats.posts == 2
    assert stats.median_engagement_rate == pytest.approx(0.075)
    assert stats.posts_per_week == 1.0


def test_niche_needs_enough_posts_per_option() -> None:
    items = [({"format": {"value": "tutorial"}}, p) for p in (1.0, 2.0, 3.0)]
    items += [({"format": {"value": "humor"}}, 9.0)]
    niche = niche_breakdown(items)
    assert niche["format"] == [{"option": "tutorial", "posts": 3, "median_performance": 2.0}]


def test_parse_json_object_tolerates_fences() -> None:
    assert parse_json_object('```json\n{"summary": "ok"}\n```') == {"summary": "ok"}
    with pytest.raises(OpenRouterError):
        parse_json_object("sem json aqui")


# ---- the loop ----------------------------------------------------------------------------


async def seed(sf: async_sessionmaker[AsyncSession], *usernames: str) -> None:
    async with sf() as session, session.begin():
        for name in usernames:
            session.add(Competitor(username=name, labels={}))


@respx.mock
async def test_scan_collects_classifies_and_reports_cost(
    keyed_app: FastAPI, keyed_settings: Settings
) -> None:
    sf = factory_of(keyed_app)
    await seed(sf, "rival", "sumido")
    http = httpx.AsyncClient()
    apify = ApifyClient(http, APIFY_TOKEN)
    router = OpenRouterClient(http, OPENROUTER_KEY)

    start = respx.post(RUNS).mock(
        return_value=httpx.Response(
            201, json={"data": {"id": "run1", "status": "RUNNING", "defaultDatasetId": "ds1"}}
        )
    )
    await run_competitors(sf, keyed_settings, apify, router)
    assert start.called
    sent = start.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {APIFY_TOKEN}"
    assert APIFY_TOKEN not in str(sent.url)
    assert json.loads(sent.content) == {"usernames": ["rival", "sumido"]}
    assert sent.url.params["maxItems"] == "2"

    respx.get(f"{APIFY_HOST}/v2/actor-runs/run1").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "run1",
                    "status": "SUCCEEDED",
                    "defaultDatasetId": "ds1",
                    "usageTotalUsd": 0.0052,
                }
            },
        )
    )
    respx.get(f"{APIFY_HOST}/v2/datasets/ds1/items").mock(
        return_value=httpx.Response(200, json=[apify_profile("rival", 10_000, [500, 100, 120, 90])])
    )
    decide = respx.post(DECISIONS).mock(return_value=httpx.Response(200, json=post_answers()))
    await run_competitors(sf, keyed_settings, apify, router)

    async with sf() as session:
        scan = await session.scalar(select(CompetitorScan))
        rival = await session.scalar(select(Competitor).where(Competitor.username == "rival"))
        gone = await session.scalar(select(Competitor).where(Competitor.username == "sumido"))
        posts = list(await session.scalars(select(CompetitorPost)))
        cost = await classify_cost(session)
    assert scan is not None and scan.status == RunStatus.SUCCEEDED
    assert (scan.profiles, scan.posts, scan.cost_usd) == (1, 4, 0.0052)
    assert rival is not None and rival.followers == 10_000 and rival.scraped_at is not None
    assert rival.labels["audience"]["value"] == "creators"
    assert rival.labels_version == TAXONOMY_VERSION
    assert gone is not None and gone.scrape_error and gone.followers is None
    assert len(posts) == 4
    assert all(p.labels["cta"]["value"] == "comment_keyword" for p in posts)
    # 4 posts + 1 profile, each one call.
    assert decide.call_count == 5
    assert cost == pytest.approx(0.001)
    body = json.loads(decide.calls[0].request.content)
    assert body["model"] == "typesafe/jev-1.14"
    assert body["questions"]["format"]["type"] == "choice"
    assert decide.calls[0].request.headers["authorization"] == f"Bearer {OPENROUTER_KEY}"

    # Nothing is due and everything is classified: the next run calls nobody.
    calls = respx.calls.call_count
    await run_competitors(sf, keyed_settings, apify, router)
    assert respx.calls.call_count == calls
    await http.aclose()


@respx.mock
async def test_failed_or_stuck_runs_are_recorded(
    keyed_app: FastAPI, keyed_settings: Settings
) -> None:
    sf = factory_of(keyed_app)
    await seed(sf, "rival")
    http = httpx.AsyncClient()
    apify = ApifyClient(http, APIFY_TOKEN)

    respx.post(RUNS).mock(
        return_value=httpx.Response(402, json={"error": {"message": "Not enough credit"}})
    )
    await run_competitors(sf, keyed_settings, apify, None)
    async with sf() as session:
        scan = await session.scalar(select(CompetitorScan))
    assert scan is not None and scan.status == RunStatus.FAILED
    assert scan.error == "Apify HTTP 402: Not enough credit"

    # A run still going after the timeout is given up on.
    async with sf() as session, session.begin():
        session.add(
            CompetitorScan(
                apify_run_id="run2",
                status=RunStatus.RUNNING,
                usernames=["rival"],
                started_at=utcnow() - SCAN_TIMEOUT - timedelta(minutes=1),
            )
        )
    respx.get(f"{APIFY_HOST}/v2/actor-runs/run2").mock(
        return_value=httpx.Response(200, json={"data": {"id": "run2", "status": "RUNNING"}})
    )
    await run_competitors(sf, keyed_settings, apify, None)
    async with sf() as session:
        stuck = await session.scalar(
            select(CompetitorScan).where(CompetitorScan.apify_run_id == "run2")
        )
    assert stuck is not None and stuck.status == RunStatus.FAILED
    await http.aclose()


async def test_own_account_is_kept_in_the_list(keyed_app: FastAPI) -> None:
    sf = factory_of(keyed_app)
    async with sf() as session, session.begin():
        session.add(
            Account(
                id=1,
                ig_user_id="1",
                username="Dono",
                token_encrypted=b"x",
                token_set_at=utcnow(),
            )
        )
    for _ in range(2):
        async with sf() as session, session.begin():
            await ensure_self(session)
    async with sf() as session:
        rows = list(await session.scalars(select(Competitor)))
    assert [(r.username, r.is_self) for r in rows] == [("dono", True)]


# ---- report ------------------------------------------------------------------------------


async def seed_collected(sf: async_sessionmaker[AsyncSession]) -> None:
    now = utcnow()
    async with sf() as session, session.begin():
        comp = Competitor(username="rival", followers=1000, scraped_at=now, labels={})
        session.add(comp)
        await session.flush()
        for i in range(4):
            session.add(
                CompetitorPost(
                    post_id=f"p{i}",
                    competitor_id=comp.id,
                    caption=f"post {i}",
                    hashtags=[],
                    posted_at=now - timedelta(days=i),
                    likes=50 * (i + 1),
                    comments=1,
                    labels={"format": {"value": "tutorial", "confidence": 0.9}},
                    labels_version=TAXONOMY_VERSION,
                )
            )


@respx.mock
async def test_report_is_stored_and_bad_answers_fail(
    keyed_app: FastAPI, keyed_settings: Settings
) -> None:
    sf = factory_of(keyed_app)
    await seed_collected(sf)
    http = httpx.AsyncClient()
    router = OpenRouterClient(http, OPENROUTER_KEY)
    content = {
        "summary": "Tutoriais dominam.",
        "what_works": ["tutoriais curtos"],
        "gaps": ["ninguém mostra bastidores"],
        "competitors": [{"username": "rival", "technologies": ["n8n"]}],
        "ideas": [{"title": "Meu setup", "keyword": "setup"}],
    }
    chat = respx.post(CHAT).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"cost": 0.03},
            },
        )
    )
    async with sf() as session, session.begin():
        session.add(CompetitorReport(id=1, model="m", status=RunStatus.RUNNING))
        session.add(CompetitorReport(id=2, model="m", status=RunStatus.RUNNING))
    await generate_report(sf, keyed_settings, router, 1)
    sent = json.loads(chat.calls.last.request.content)
    assert sent["model"] == keyed_settings.competitors_report_model
    data = json.loads(sent["messages"][1]["content"])
    assert data["concorrentes"][0]["perfil"] == "@rival"
    assert data["o_que_performa_no_nicho"]["Formato"][0]["opcao"] == "Tutorial"

    chat.mock(return_value=httpx.Response(200, json={"choices": [{"message": {"content": "oi"}}]}))
    await generate_report(sf, keyed_settings, router, 2)
    async with sf() as session:
        done = await session.get(CompetitorReport, 1)
        failed = await session.get(CompetitorReport, 2)
    assert done is not None and done.status == RunStatus.SUCCEEDED and done.cost_usd == 0.03
    assert done.content is not None and done.content["ideas"][0]["keyword"] == "setup"
    assert failed is not None and failed.status == RunStatus.FAILED and failed.error
    await http.aclose()


# ---- API ---------------------------------------------------------------------------------


async def test_api_without_keys_says_what_is_off(api_client: httpx.AsyncClient) -> None:
    body = (await api_client.get("/api/v1/competitors")).json()
    assert body["collect_enabled"] is False and body["classify_enabled"] is False
    assert body["taxonomy"]["post"][0]["key"] == "format"
    assert (await api_client.post("/api/v1/competitors/scan")).status_code == 409
    assert (await api_client.post("/api/v1/competitors/report")).status_code == 409


async def test_api_add_list_and_remove(keyed_api: httpx.AsyncClient) -> None:
    created = await keyed_api.post(
        "/api/v1/competitors", json={"username": "https://instagram.com/Rival/"}
    )
    assert created.status_code == 201
    again = await keyed_api.post("/api/v1/competitors", json={"username": "@rival"})
    assert again.status_code == 409
    bad = await keyed_api.post("/api/v1/competitors", json={"username": "não é perfil"})
    assert bad.status_code == 422
    body = (await keyed_api.get("/api/v1/competitors")).json()
    assert body["collect_enabled"] and body["classify_enabled"]
    assert [c["username"] for c in body["competitors"]] == ["rival"]
    competitor_id = body["competitors"][0]["id"]
    assert (await keyed_api.delete(f"/api/v1/competitors/{competitor_id}")).status_code == 204
    assert (await keyed_api.delete(f"/api/v1/competitors/{competitor_id}")).status_code == 404
    # No competitor collected: nothing to write a report about.
    assert (await keyed_api.post("/api/v1/competitors/report")).status_code == 409


async def test_api_limit(database_url: str) -> None:
    from app.factory import create_app

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url=database_url, competitors_max=1, **strong_secrets()
    )
    application = create_app(settings)
    token = settings.api_admin_token.get_secret_value()
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
            headers={"authorization": f"Bearer {token}"},
        ) as c,
    ):
        assert (await c.post("/api/v1/competitors", json={"username": "a1"})).status_code == 201
        refused = await c.post("/api/v1/competitors", json={"username": "a2"})
        assert refused.status_code == 409


async def test_competitor_routes_need_the_token(client: httpx.AsyncClient) -> None:
    for method, path in (
        ("GET", "/api/v1/competitors"),
        ("POST", "/api/v1/competitors"),
        ("POST", "/api/v1/competitors/scan"),
        ("GET", "/api/v1/competitors/report"),
        ("POST", "/api/v1/competitors/report"),
    ):
        response = await client.request(method, path, headers={"authorization": "Bearer nope"})
        assert response.status_code == 401


@pytest.fixture
async def api_client(client: httpx.AsyncClient, settings: Settings) -> httpx.AsyncClient:
    client.headers["authorization"] = f"Bearer {settings.api_admin_token.get_secret_value()}"
    return client


def test_parse_json_object_accepts_raw_line_breaks_and_says_where_it_broke() -> None:
    assert parse_json_object('{"summary": "linha 1\nlinha 2"}') == {"summary": "linha 1\nlinha 2"}
    with pytest.raises(OpenRouterError, match="character"):
        parse_json_object('{"summary": "ok",, "gaps": []}')


@respx.mock
async def test_report_retries_once_with_the_problem(
    keyed_app: FastAPI, keyed_settings: Settings
) -> None:
    sf = factory_of(keyed_app)
    await seed_collected(sf)
    http = httpx.AsyncClient()
    router = OpenRouterClient(http, OPENROUTER_KEY)
    cut = {
        "choices": [{"message": {"content": '{"summary": "começo'}, "finish_reason": "length"}],
        "usage": {"cost": 0.01},
    }
    fixed = {
        "choices": [{"message": {"content": '{"summary": "ok", "ideas": []}'}}],
        "usage": {"cost": 0.02},
    }
    chat = respx.post(CHAT).mock(
        side_effect=[httpx.Response(200, json=cut), httpx.Response(200, json=fixed)]
    )
    async with sf() as session, session.begin():
        session.add(CompetitorReport(id=1, model="m", status=RunStatus.RUNNING))
    await generate_report(sf, keyed_settings, router, 1)

    first = json.loads(chat.calls[0].request.content)
    assert first["response_format"]["type"] == "json_schema"
    assert "summary" in first["response_format"]["json_schema"]["schema"]["properties"]
    second = json.loads(chat.calls[1].request.content)
    assert second["messages"][2] == {"role": "assistant", "content": '{"summary": "começo'}
    assert "token limit" in second["messages"][3]["content"]
    async with sf() as session:
        row = await session.get(CompetitorReport, 1)
    assert row is not None and row.status == RunStatus.SUCCEEDED
    assert row.cost_usd == pytest.approx(0.03)
    assert row.content is not None and row.content["summary"] == "ok"
    await http.aclose()
