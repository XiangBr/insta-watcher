import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import func, select, update

from app.config import Settings
from app.db import utcnow
from app.dispatcher import dispatch_pending
from app.matching import KeywordMatcher
from app.meta.client import GRAPH_HOST, MetaClient
from app.models import (
    Account,
    AccountStatus,
    Campaign,
    Delivery,
    DeliveryStatus,
    Job,
    JobStatus,
    JobType,
    SendEvent,
)
from app.queue import recover_stale
from app.ratelimit import PRIVATE_REPLY, RateLimiter
from app.tokens import cipher_for
from app.worker import Worker
from tests.factories import OWNER_ID, add_campaign, add_webhook_event, comment_payload

TOKEN = "IGAAworkertoken" + "z" * 40
SEND = f"{GRAPH_HOST}/v25.0/me/messages"


def graph_error(code: int) -> dict[str, Any]:
    return {"error": {"message": "nope", "type": "OAuthException", "code": code}}


@pytest.fixture
async def meta() -> AsyncIterator[MetaClient]:
    async with httpx.AsyncClient() as http:
        yield MetaClient(http, "v25.0")


@pytest.fixture
async def ready(app: FastAPI, settings: Settings) -> FastAPI:
    """App with an active account and one link campaign."""
    async with app.state.session_factory() as session, session.begin():
        session.add(
            Account(
                id=1,
                ig_user_id=OWNER_ID,
                username="dono",
                token_encrypted=cipher_for(settings).encrypt(TOKEN),
                token_set_at=utcnow(),
            )
        )
    await add_campaign(app.state.session_factory)
    return app


def make_worker(app: FastAPI, settings: Settings, meta: MetaClient, limit: int = 750) -> Worker:
    limiter = RateLimiter(app.state.session_factory, limit)
    return Worker(app.state.session_factory, settings, meta, limiter)


async def comment(app: FastAPI, comment_id: str = "c_1", **kwargs: Any) -> None:
    await add_webhook_event(app.state.session_factory, comment_payload(comment_id, **kwargs))
    await dispatch_pending(app.state.session_factory, KeywordMatcher())


async def one(app: FastAPI, model: Any) -> Any:
    async with app.state.session_factory() as session:
        return (await session.execute(select(model))).scalar_one()


async def send_events(app: FastAPI) -> int:
    async with app.state.session_factory() as session:
        return int((await session.execute(select(func.count(SendEvent.id)))).scalar_one())


# ---- happy path and idempotency ----


@respx.mock
async def test_private_reply_sent(ready: FastAPI, settings: Settings, meta: MetaClient) -> None:
    route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m_1"}))
    await comment(ready, username="ana")
    await make_worker(ready, settings, meta).run_until_idle()

    delivery, job = await one(ready, Delivery), await one(ready, Job)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.dm_message_id == "m_1"
    assert job.status == JobStatus.DONE
    assert await send_events(ready) == 1
    body = json.loads(route.calls.last.request.content)
    assert body["recipient"] == {"comment_id": "c_1"}
    assert "https://exemplo.com" in body["message"]["text"]


@respx.mock
async def test_already_sent_delivery_is_not_sent_again(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m_1"}))
    await comment(ready)
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(update(Delivery).values(status=DeliveryStatus.SENT))
    await make_worker(ready, settings, meta).run_until_idle()
    assert route.call_count == 0
    assert (await one(ready, Job)).status == JobStatus.DONE


# ---- error classification drives the next step ----


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.ReadTimeout("slow"),
        httpx.Response(500, json=graph_error(1)),
        httpx.Response(200, json={"recipient_id": "x"}),
    ],
)
async def test_ambiguous_failure_becomes_unconfirmed_and_is_never_retried(
    ready: FastAPI, settings: Settings, meta: MetaClient, response: Any
) -> None:
    if isinstance(response, Exception):
        route = respx.post(SEND).mock(side_effect=response)
    else:
        route = respx.post(SEND).mock(return_value=response)
    await comment(ready)
    worker = make_worker(ready, settings, meta)
    await worker.run_until_idle()
    await worker.run_until_idle()

    assert route.call_count == 1
    assert (await one(ready, Delivery)).status == DeliveryStatus.UNCONFIRMED
    assert (await one(ready, Job)).status == JobStatus.UNCONFIRMED
    assert await send_events(ready) == 1  # the slot stays: the DM may have left


@respx.mock
async def test_rate_limit_rejection_backs_off_and_releases_slot(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.post(SEND).mock(return_value=httpx.Response(400, json=graph_error(4)))
    await comment(ready)
    await make_worker(ready, settings, meta).run_until_idle()

    job = await one(ready, Job)
    assert job.status == JobStatus.PENDING
    assert job.attempts == 1
    assert job.send_started_at is None
    assert timedelta(minutes=4) < job.run_at - utcnow() <= timedelta(minutes=5)
    assert await send_events(ready) == 0


@respx.mock
async def test_permanent_rejection_fails_without_retry(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.post(SEND).mock(return_value=httpx.Response(403, json=graph_error(10)))
    await comment(ready)
    await make_worker(ready, settings, meta).run_until_idle()

    assert (await one(ready, Delivery)).status == DeliveryStatus.FAILED
    assert (await one(ready, Job)).status == JobStatus.FAILED
    assert await send_events(ready) == 0


@respx.mock
async def test_token_rejection_pauses_until_new_token(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.post(SEND).mock(return_value=httpx.Response(401, json=graph_error(190)))
    await comment(ready)
    await make_worker(ready, settings, meta).run_until_idle()

    assert (await one(ready, Account)).status == AccountStatus.TOKEN_INVALID
    job = await one(ready, Job)
    assert job.status == JobStatus.PENDING
    assert job.attempts == 0
    assert (await one(ready, Delivery)).status == DeliveryStatus.PENDING


@respx.mock
async def test_connection_failure_retries(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.post(SEND).mock(side_effect=httpx.ConnectError("down"))
    await comment(ready)
    await make_worker(ready, settings, meta).run_until_idle()
    job = await one(ready, Job)
    assert job.status == JobStatus.PENDING
    assert await send_events(ready) == 0


@respx.mock
async def test_button_rejection_falls_back_to_link_in_text(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(
            update(Campaign).values(
                dm_message="Segue o material", link_url="https://x.com/m", link_button_label="Abrir"
            )
        )
    route = respx.post(SEND).mock(
        side_effect=[
            httpx.Response(400, json=graph_error(100)),
            httpx.Response(200, json={"message_id": "m_2"}),
        ]
    )
    await comment(ready)
    await make_worker(ready, settings, meta).run_until_idle()

    first, second = (json.loads(c.request.content)["message"] for c in route.calls)
    assert "attachment" in first
    assert second == {"text": "Segue o material\nhttps://x.com/m"}
    assert (await one(ready, Delivery)).status == DeliveryStatus.SENT
    assert await send_events(ready) == 1


# ---- rate limit ----


@respx.mock
async def test_751st_private_reply_waits_for_the_window(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m"}))
    now = utcnow()
    oldest = now - timedelta(minutes=50)
    async with ready.state.session_factory() as session, session.begin():
        session.add_all(
            SendEvent(kind=PRIVATE_REPLY, sent_at=oldest + timedelta(seconds=i)) for i in range(750)
        )
    await comment(ready)
    # A fresh limiter is a restarted process: the count lives in SQLite, not in memory.
    await make_worker(ready, settings, meta, limit=750).run_until_idle()

    assert route.call_count == 0
    job = await one(ready, Job)
    assert job.status == JobStatus.PENDING
    assert job.attempts == 0
    assert abs(job.run_at - (oldest + timedelta(hours=1))) < timedelta(seconds=1)


@respx.mock
async def test_hourly_limit_sends_up_to_the_cap(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m"}))
    for i in range(5):
        await comment(ready, f"c_{i}")
    await make_worker(ready, settings, meta, limit=3).run_until_idle()

    assert route.call_count == 3
    async with ready.state.session_factory() as session:
        statuses = (await session.execute(select(Delivery.status))).scalars().all()
    assert sorted(statuses) == ["PENDING", "PENDING", "SENT", "SENT", "SENT"]


# ---- crash recovery ----


async def test_crash_recovery_uses_send_started_at(ready: FastAPI) -> None:
    await comment(ready, "c_1")
    await comment(ready, "c_2")
    async with ready.state.session_factory() as session, session.begin():
        jobs = (await session.execute(select(Job).order_by(Job.id))).scalars().all()
        for job in jobs:
            job.status = JobStatus.RUNNING
            job.locked_until = utcnow() + timedelta(minutes=2)
        jobs[1].send_started_at = utcnow()

    result = await recover_stale(ready.state.session_factory, at_boot=True)

    assert (result.requeued, result.unconfirmed) == (1, 1)
    async with ready.state.session_factory() as session:
        jobs = (await session.execute(select(Job).order_by(Job.id))).scalars().all()
        deliveries = (await session.execute(select(Delivery).order_by(Delivery.id))).scalars().all()
    assert [j.status for j in jobs] == [JobStatus.PENDING, JobStatus.UNCONFIRMED]
    assert deliveries[1].status == DeliveryStatus.UNCONFIRMED


# ---- account switch and public reply ----


@respx.mock
async def test_paused_account_sends_nothing(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m"}))
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(update(Account).values(sending_paused=True))
    await comment(ready)
    await make_worker(ready, settings, meta).run_until_idle()
    assert route.call_count == 0
    assert (await one(ready, Job)).status == JobStatus.PENDING


@respx.mock
async def test_public_reply_is_independent_of_dm_failure(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(
            update(Campaign).values(
                public_reply_enabled=True,
                public_reply_messages=["Te chamei no direct, {username}!"],
            )
        )
    respx.post(SEND).mock(return_value=httpx.Response(403, json=graph_error(10)))
    reply = respx.post(f"{GRAPH_HOST}/v25.0/c_1/replies").mock(
        return_value=httpx.Response(200, json={"id": "r_1"})
    )
    await comment(ready, username="ana")
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(update(Job).values(run_at=utcnow() - timedelta(seconds=1)))
    await make_worker(ready, settings, meta).run_until_idle()

    delivery = await one(ready, Delivery)
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.public_reply_status == DeliveryStatus.SENT
    assert json.loads(reply.calls.last.request.content) == {"message": "Te chamei no direct, ana!"}
    async with ready.state.session_factory() as session:
        types = (await session.execute(select(Job.type, Job.status))).all()
    assert (JobType.PUBLIC_REPLY, JobStatus.DONE) in types
