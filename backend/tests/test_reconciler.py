from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import select, update

from app.config import Settings
from app.db import utcnow
from app.dispatcher import dispatch_pending
from app.matching import KeywordMatcher
from app.meta.client import GRAPH_HOST, MetaClient
from app.models import Account, Campaign, Delivery, Origin
from app.reconciler import reconcile
from app.tokens import cipher_for
from tests.factories import OWNER_ID, POST_ID, add_campaign, add_webhook_event, comment_payload

COMMENTS = f"{GRAPH_HOST}/v25.0/{POST_ID}/comments"
matcher = KeywordMatcher()


@pytest.fixture
async def meta() -> AsyncIterator[MetaClient]:
    async with httpx.AsyncClient() as http:
        yield MetaClient(http, "v25.0")


@pytest.fixture
async def ready(app: FastAPI, settings: Settings) -> FastAPI:
    factory = app.state.session_factory
    async with factory() as session, session.begin():
        session.add(
            Account(
                id=1,
                ig_user_id=OWNER_ID,
                username="dono",
                token_encrypted=cipher_for(settings).encrypt("IGAA" + "t" * 40),
                token_set_at=utcnow(),
            )
        )
    await add_campaign(factory)
    # The campaign predates every comment in these tests.
    async with factory() as session, session.begin():
        await session.execute(update(Campaign).values(created_at=utcnow() - timedelta(days=10)))
    return app


def stamp(hours_ago: float) -> str:
    return (utcnow() - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def api_comment(
    comment_id: str,
    text: str = "link",
    *,
    hours_ago: float = 1,
    from_id: str = "fan_1",
    replies_from: list[str] | None = None,
) -> dict[str, Any]:
    comment: dict[str, Any] = {
        "id": comment_id,
        "text": text,
        "timestamp": stamp(hours_ago),
        "from": {"id": from_id, "username": f"u_{from_id}"},
    }
    if replies_from:
        comment["replies"] = {"data": [{"id": "r", "from": {"id": f}} for f in replies_from]}
    return comment


def page(comments: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"data": comments})


async def deliveries(app: FastAPI) -> list[Delivery]:
    async with app.state.session_factory() as session:
        return list((await session.execute(select(Delivery).order_by(Delivery.id))).scalars())


@respx.mock
async def test_missed_matching_comment_is_queued_as_polling(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(COMMENTS).mock(return_value=page([api_comment("c_1")]))
    assert await reconcile(ready.state.session_factory, settings, meta, matcher) == 1
    (delivery,) = await deliveries(ready)
    assert delivery.origin == Origin.POLLING
    assert delivery.source_id == "c_1"


@respx.mock
async def test_comment_already_answered_by_owner_is_skipped(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(COMMENTS).mock(return_value=page([api_comment("c_1", replies_from=[OWNER_ID])]))
    assert await reconcile(ready.state.session_factory, settings, meta, matcher) == 0
    assert await deliveries(ready) == []


@respx.mock
async def test_comment_seen_by_webhook_is_not_queued_twice(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_webhook_event(ready.state.session_factory, comment_payload("c_1"))
    await dispatch_pending(ready.state.session_factory, matcher)
    respx.get(COMMENTS).mock(return_value=page([api_comment("c_1")]))

    assert await reconcile(ready.state.session_factory, settings, meta, matcher) == 0
    assert len(await deliveries(ready)) == 1


@respx.mock
async def test_respects_the_cap_per_sweep(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(COMMENTS).mock(
        return_value=page([api_comment(f"c_{i}", from_id=f"f{i}") for i in range(40)])
    )
    capped = settings.model_copy(update={"reconcile_max_per_sweep": 30})
    assert await reconcile(ready.state.session_factory, capped, meta, matcher) == 30
    assert len(await deliveries(ready)) == 30


@respx.mock
async def test_skips_old_own_unmatched_and_pre_campaign_comments(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(update(Campaign).values(created_at=utcnow() - timedelta(hours=10)))
    respx.get(COMMENTS).mock(
        return_value=page(
            [
                api_comment("old", hours_ago=73),
                api_comment("before_campaign", hours_ago=11),
                api_comment("mine", from_id=OWNER_ID),
                api_comment("nomatch", text="lindo"),
                api_comment("ok"),
            ]
        )
    )
    assert await reconcile(ready.state.session_factory, settings, meta, matcher) == 1
    assert [d.source_id for d in await deliveries(ready)] == ["ok"]


@respx.mock
async def test_meta_failure_is_reported_not_raised(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(COMMENTS).mock(side_effect=httpx.ReadTimeout("slow"))
    assert await reconcile(ready.state.session_factory, settings, meta, matcher) == 0
