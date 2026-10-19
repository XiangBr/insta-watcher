from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import utcnow
from app.insights import (
    SNAPSHOT_STATUS,
    SNAPSHOT_UNSUPPORTED,
    comment_terms,
    day_bounds,
    is_permission_error,
    local_day,
    media_kind,
    merge_terms,
    parse_media_insights,
    parse_online_followers,
    parse_totals,
    sync_insights,
)
from app.maintenance import apply_retention
from app.meta.client import GRAPH_HOST
from app.meta.errors import classify_error_response
from app.models import Account, InsightComment, InsightDay, InsightMedia, InsightSnapshot
from app.tokens import cipher_for
from tests.factories import OWNER_ID

API = f"{GRAPH_HOST}/v25.0"
REEL, PHOTO = "900001", "900002"


def stamp(days_ago: float) -> str:
    return (utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def graph_error(code: int, message: str) -> httpx.Response:
    return httpx.Response(
        400, json={"error": {"message": message, "type": "OAuthException", "code": code}}
    )


# ---- pure helpers ------------------------------------------------------------------------


def test_comment_terms_drop_mentions_stopwords_and_accents() -> None:
    terms = comment_terms("@amiga olha que PREÇO bom!! Quero o preço da receita 🔥 123")
    assert terms["preco"] == 2
    assert terms["receita"] == 1
    assert "amiga" not in terms  # the mention is a person, not a topic
    assert "que" not in terms and "da" not in terms and "123" not in terms


def test_merge_terms_keeps_the_most_common() -> None:
    from collections import Counter

    merged = merge_terms({"bolo": 2}, Counter({"bolo": 1, "receita": 5}))
    assert merged == {"receita": 5, "bolo": 3}


@pytest.mark.parametrize(
    ("media_type", "product", "kind"),
    [
        ("VIDEO", "REELS", "REELS"),
        ("VIDEO", None, "REELS"),
        ("IMAGE", None, "FEED"),
        ("CAROUSEL_ALBUM", "FEED", "FEED"),
        ("VIDEO", "FEED", "FEED"),
    ],
)
def test_media_kind(media_type: str, product: str | None, kind: str) -> None:
    assert media_kind(media_type, product) == kind


def test_local_days_follow_the_offset() -> None:
    late_night_utc = datetime(2026, 10, 2, 2, 0, tzinfo=UTC)  # 23:00 of the 1st in Brasília
    assert local_day(late_night_utc, -180) == date(2026, 10, 1)
    start, end = day_bounds(date(2026, 10, 1), -180)
    assert start == datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    assert end - start == timedelta(days=1)


def test_parse_media_insights_and_totals() -> None:
    body = {
        "data": [
            {"name": "reach", "period": "lifetime", "values": [{"value": 120}]},
            {"name": "saved", "values": [{"value": 7}]},
            {"name": "views", "total_value": {"value": 300}},
        ]
    }
    assert parse_media_insights(body) == {"reach": 120, "saved": 7, "views": 300}
    totals = {"data": [{"name": "reach", "total_value": {"value": 50}}, {"name": "x"}]}
    assert parse_totals(totals) == {"reach": 50}


def test_online_followers_are_moved_from_pacific_to_local() -> None:
    hours = dict.fromkeys(map(str, range(24)), 0)
    hours["12"] = 100  # 12:00 Pacific daylight time (UTC-7) is 16:00 in Brasília
    body = {"data": [{"values": [{"value": hours, "end_time": "2026-07-10T07:00:00+0000"}]}]}
    result = parse_online_followers(body, -180)
    assert result is not None
    assert result["16"] == 100
    assert result["12"] == 0


def test_permission_error_needs_the_word() -> None:
    no_permission = classify_error_response(
        403, {"error": {"code": 10, "message": "Application does not have permission"}}
    )
    few_viewers = classify_error_response(
        400, {"error": {"code": 10, "message": "Not enough viewers for the media"}}
    )
    assert is_permission_error(no_permission)
    assert not is_permission_error(few_viewers)


# ---- the sync, against a fake Graph API --------------------------------------------------


@pytest.fixture
def sf(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    return factory


@pytest.fixture
async def account(sf: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    async with sf() as session, session.begin():
        session.add(
            Account(
                id=1,
                ig_user_id=OWNER_ID,
                username="dono",
                token_encrypted=cipher_for(settings).encrypt("IGAA" + "t" * 40),
                token_set_at=utcnow(),
            )
        )


def media_list() -> dict[str, Any]:
    return {
        "data": [
            {
                "id": REEL,
                "media_type": "VIDEO",
                "media_product_type": "REELS",
                "timestamp": stamp(2),
                "caption": "Receita de bolo #receita",
                "like_count": 40,
                "comments_count": 3,
            },
            {
                "id": PHOTO,
                "media_type": "IMAGE",
                "media_product_type": "FEED",
                "timestamp": stamp(20),
                "caption": "Foto",
                "like_count": 10,
                "comments_count": 0,
            },
        ]
    }


def insights_response(request: httpx.Request) -> httpx.Response:
    metric = request.url.params.get("metric", "")
    breakdown = request.url.params.get("breakdown")
    if breakdown:
        results = {
            "follow_type": [("FOLLOWER", 3), ("NON_FOLLOWER", 1)],
            "age": [("18-24", 30), ("25-34", 50)],
            "gender": [("F", 60), ("M", 20)],
            "city": [("São Paulo, São Paulo (state)", 40)],
            "country": [("BR", 75)],
            "media_product_type": [("REEL", 900), ("POST", 100)],
        }[breakdown]
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": metric,
                        "total_value": {
                            "breakdowns": [
                                {
                                    "dimension_keys": [breakdown],
                                    "results": [
                                        {"dimension_values": [k], "value": v} for k, v in results
                                    ],
                                }
                            ]
                        },
                    }
                ]
            },
        )
    if metric == "online_followers":
        return graph_error(100, "metric not supported")
    names = metric.split(",")
    return httpx.Response(
        200, json={"data": [{"name": n, "total_value": {"value": 10}} for n in names]}
    )


def comments_page() -> dict[str, Any]:
    return {
        "data": [
            {
                "id": "k1",
                "text": "Qual o preço?",
                "timestamp": stamp(1),
                "from": {"id": "fan_1", "username": "ana"},
            },
            {
                "id": "k2",
                "text": "@bia olha essa receita",
                "timestamp": stamp(1),
                "from": {"id": "fan_2", "username": "bia"},
            },
            {
                "id": "k3",
                "text": "obrigado!",
                "timestamp": stamp(1),
                "from": {"id": OWNER_ID, "username": "dono"},
            },
        ]
    }


def mock_graph(*, insights: Any = insights_response) -> None:
    respx.get(f"{API}/me").mock(
        return_value=httpx.Response(200, json={"username": "dono", "followers_count": 1234})
    )
    respx.get(f"{API}/me/media").mock(return_value=httpx.Response(200, json=media_list()))
    respx.get(f"{API}/me/insights").mock(side_effect=insights)
    respx.get(f"{API}/{REEL}/insights").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"name": "reach", "values": [{"value": 500}]},
                    {"name": "saved", "values": [{"value": 25}]},
                ]
            },
        )
    )
    respx.get(f"{API}/{PHOTO}/insights").mock(
        return_value=httpx.Response(
            200, json={"data": [{"name": "reach", "values": [{"value": 80}]}]}
        )
    )
    respx.get(f"{API}/{REEL}/comments").mock(return_value=httpx.Response(200, json=comments_page()))


@respx.mock
async def test_sync_fills_posts_days_audience_and_comments(
    sf: async_sessionmaker[AsyncSession], settings: Settings, account: None, app: FastAPI
) -> None:
    mock_graph()
    result = await sync_insights(sf, settings, app.state.meta)
    assert not result.missing_permission
    assert result.media_listed == 2
    async with sf() as session:
        reel = await session.get(InsightMedia, REEL)
        assert reel is not None
        assert reel.metrics["reach"] == 500
        assert reel.metrics["comment_questions"] == 1
        assert reel.metrics["comment_mentions"] == 1
        assert reel.terms["receita"] == 1 and "bia" not in reel.terms
        comments = (await session.execute(select(InsightComment))).scalars().all()
        # The owner's own reply is not activity from the audience.
        assert sorted(c.comment_id for c in comments) == ["k1", "k2"]
        today = local_day(utcnow(), settings.insights_utc_offset_minutes).isoformat()
        day = await session.get(InsightDay, today)
        assert day is not None and day.followers_count == 1234
        assert day.metrics["follows"] == 3 and day.metrics["unfollows"] == 1
        audience = await session.get(InsightSnapshot, "audience")
        assert audience is not None
        assert audience.data["followers"]["gender"] == {"F": 60, "M": 20}
        assert audience.data["online_followers"] is None


@respx.mock
async def test_a_second_sync_does_not_count_comments_twice(
    sf: async_sessionmaker[AsyncSession], settings: Settings, account: None, app: FastAPI
) -> None:
    mock_graph()
    await sync_insights(sf, settings, app.state.meta)
    async with sf() as session, session.begin():
        reel = await session.get(InsightMedia, REEL)
        assert reel is not None
        reel.comments_seen_count = 0  # pretend a new comment arrived
    await sync_insights(sf, settings, app.state.meta)
    async with sf() as session:
        reel = await session.get(InsightMedia, REEL)
        assert reel is not None
        assert reel.terms["receita"] == 1
        assert reel.metrics["comment_questions"] == 1


@respx.mock
async def test_without_insights_permission_posts_and_comments_still_sync(
    sf: async_sessionmaker[AsyncSession], settings: Settings, account: None, app: FastAPI
) -> None:
    mock_graph(
        insights=graph_error(10, "Application does not have permission for this action"),
    )
    result = await sync_insights(sf, settings, app.state.meta)
    assert result.missing_permission
    async with sf() as session:
        assert len((await session.execute(select(InsightMedia))).scalars().all()) == 2
        assert len((await session.execute(select(InsightComment))).scalars().all()) == 2
        status = await session.get(InsightSnapshot, SNAPSHOT_STATUS)
        assert status is not None and status.data["missing_permission"] is True


@respx.mock
async def test_refused_metrics_are_asked_one_by_one_and_remembered(
    sf: async_sessionmaker[AsyncSession], settings: Settings, account: None, app: FastAPI
) -> None:
    mock_graph()

    def reel_insights(request: httpx.Request) -> httpx.Response:
        metric = request.url.params["metric"]
        if "," in metric or metric == "reels_skip_rate":
            return graph_error(100, "metric not supported for this media")
        return httpx.Response(200, json={"data": [{"name": metric, "values": [{"value": 5}]}]})

    respx.get(f"{API}/{REEL}/insights").mock(side_effect=reel_insights)
    await sync_insights(sf, settings, app.state.meta)
    async with sf() as session:
        reel = await session.get(InsightMedia, REEL)
        assert reel is not None and reel.metrics["reach"] == 5
        assert "reels_skip_rate" not in reel.metrics
        unsupported = await session.get(InsightSnapshot, SNAPSHOT_UNSUPPORTED)
        assert unsupported is not None
        assert unsupported.data["media_REELS"] == ["reels_skip_rate"]


@respx.mock
async def test_the_call_budget_ends_the_run_early(
    sf: async_sessionmaker[AsyncSession], settings: Settings, account: None, app: FastAPI
) -> None:
    mock_graph()
    tight = settings.model_copy(update={"insights_max_calls_per_sync": 20})
    result = await sync_insights(sf, tight, app.state.meta)
    assert result.partial
    assert result.calls == 20


async def test_sync_without_account_does_nothing(
    sf: async_sessionmaker[AsyncSession], settings: Settings, app: FastAPI
) -> None:
    result = await sync_insights(sf, settings, app.state.meta)
    assert result.calls == 0


async def test_retention_drops_who_commented_but_keeps_when(
    sf: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    old = utcnow() - timedelta(days=settings.retention_delivery_days + 1)
    async with sf() as session, session.begin():
        session.add(
            InsightComment(
                comment_id="old", media_id=REEL, commented_at=old, author_id="1", username="ana"
            )
        )
        session.add(
            InsightComment(
                comment_id="new",
                media_id=REEL,
                commented_at=utcnow(),
                author_id="2",
                username="bia",
            )
        )
    result = await apply_retention(sf, settings)
    assert result.commenters_anonymized == 1
    async with sf() as session:
        old_row = await session.get(InsightComment, "old")
        new_row = await session.get(InsightComment, "new")
    assert old_row is not None and old_row.username is None and old_row.author_id is None
    assert new_row is not None and new_row.username == "bia"


# ---- API ---------------------------------------------------------------------------------


@pytest.fixture
async def api(client: httpx.AsyncClient, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    client.headers["authorization"] = f"Bearer {settings.api_admin_token.get_secret_value()}"
    yield client


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/insights"),
        ("GET", "/api/v1/insights/activity"),
        ("POST", "/api/v1/insights/sync"),
    ],
)
async def test_insights_routes_need_the_token(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, headers={"authorization": "Bearer nope"})
    assert response.status_code == 401


async def test_insights_is_empty_before_any_sync(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/v1/insights")).json()
    assert body["posts"] == [] and body["profile"] is None and body["audience"] is None
    assert body["sync"]["finished_at"] is None
    assert body["utc_offset_minutes"] == -180


@respx.mock
async def test_insights_and_activity_after_a_sync(
    api: httpx.AsyncClient,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    account: None,
    app: FastAPI,
) -> None:
    mock_graph()
    await sync_insights(sf, settings, app.state.meta)
    body = (await api.get("/api/v1/insights")).json()
    assert [p["id"] for p in body["posts"]] == [REEL, PHOTO]
    assert body["posts"][0]["kind"] == "REELS"
    assert body["posts"][0]["metrics"]["reach"] == 500
    assert body["profile"]["followers_count"] == 1234
    assert body["audience"]["engaged"]["age"] == {"18-24": 30, "25-34": 50}
    assert body["sync"]["finished_at"] is not None

    activity = (await api.get("/api/v1/insights/activity")).json()
    assert activity["total_comments"] == 2
    assert activity["unique_commenters"] == 2
    assert {c["username"] for c in activity["top_commenters"]} == {"ana", "bia"}
    assert sum(h["count"] for h in activity["hours"]) == 2


async def test_activity_rejects_naive_and_inverted_ranges(api: httpx.AsyncClient) -> None:
    naive = await api.get("/api/v1/insights/activity", params={"since": "2026-10-01T00:00:00"})
    assert naive.status_code == 422
    inverted = await api.get(
        "/api/v1/insights/activity",
        params={"since": "2026-10-02T00:00:00Z", "until": "2026-10-01T00:00:00Z"},
    )
    assert inverted.status_code == 422


async def test_sync_button_needs_an_account(api: httpx.AsyncClient) -> None:
    assert (await api.post("/api/v1/insights/sync")).status_code == 409
