"""Replies to a story: the dispatcher turns them into a direct message job, the worker sends it
by user id inside the 24 h window."""

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
from app.dispatcher import dispatch_pending, parse_story_replies
from app.matching import KeywordMatcher
from app.meta.client import GRAPH_HOST, MetaClient
from app.models import (
    Account,
    Delivery,
    DeliveryStatus,
    Job,
    JobStatus,
    JobType,
    SendEvent,
    Trigger,
    WebhookEvent,
    WebhookStatus,
)
from app.queue import recover_stale
from app.ratelimit import RateLimiter
from app.tokens import cipher_for
from app.worker import Worker
from tests.factories import (
    FAN_ID,
    OWNER_ID,
    add_campaign,
    add_webhook_event,
    comment_payload,
    story_reply_payload,
)

TOKEN = "IGAAstorytoken" + "s" * 40
SEND = f"{GRAPH_HOST}/v25.0/me/messages"
PROFILE = f"{GRAPH_HOST}/v25.0/{FAN_ID}"
matcher = KeywordMatcher()


def story_campaign(**overrides: Any) -> dict[str, Any]:
    return {
        "post_id": None,
        "match_any_post": False,
        "trigger_comments": False,
        "trigger_story_replies": True,
        "dm_message": "Oi {username}! Aqui está",
        "link_url": "https://exemplo.com",
        "link_button_label": "Abrir",
        **overrides,
    }


async def rows(app: FastAPI, model: Any) -> list[Any]:
    async with app.state.session_factory() as session:
        return list((await session.execute(select(model))).scalars().all())


# ---- parsing -------------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["messaging", "changes"])
def test_both_webhook_shapes_are_read(shape: str) -> None:
    [reply] = parse_story_replies(story_reply_payload(shape=shape))
    assert reply.mid == "mid_1"
    assert reply.sender_id == FAN_ID
    assert reply.story_id == "17900000000000001"
    assert reply.owner_id == OWNER_ID


@pytest.mark.parametrize(
    "kwargs",
    [
        {"is_echo": True},
        {"is_deleted": True},
        {"is_self": True},
        {"text": None},
        {"text": "   "},
        {"story_id": None},  # a plain DM, not a story reply
    ],
)
@pytest.mark.parametrize("shape", ["messaging", "changes"])
def test_what_must_never_trigger_is_skipped(kwargs: dict[str, Any], shape: str) -> None:
    assert list(parse_story_replies(story_reply_payload(shape=shape, **kwargs))) == []


def test_other_payloads_are_ignored() -> None:
    assert list(parse_story_replies(comment_payload())) == []
    assert list(parse_story_replies({"object": "page", "entry": []})) == []
    assert list(parse_story_replies({})) == []


# ---- dispatcher ----------------------------------------------------------------------------


async def test_matching_story_reply_creates_a_direct_message_job(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory, **story_campaign(keywords=["link"]))
    await add_webhook_event(factory, story_reply_payload(text="Quero o LINK 🔥"))

    assert await dispatch_pending(factory, matcher) == 1

    [delivery] = await rows(app, Delivery)
    [job] = await rows(app, Job)
    [event] = await rows(app, WebhookEvent)
    assert delivery.trigger == Trigger.STORY_REPLY
    assert delivery.source_id == "mid_1"
    assert delivery.media_id == "17900000000000001"
    assert delivery.actor_id == FAN_ID
    assert delivery.actor_username is None
    assert delivery.matched_keyword == "link"
    assert delivery.public_reply_status is None
    assert job.type == JobType.DIRECT_MESSAGE
    assert job.dedupe_key == f"direct:{delivery.id}"
    assert event.status == WebhookStatus.PROCESSED


async def test_no_keyword_means_no_dm(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory, **story_campaign(keywords=["link"]))
    for mid, text in [("m1", "❤️"), ("m2", "kkkkk"), ("m3", "lindo!")]:
        await add_webhook_event(factory, story_reply_payload(mid=mid, text=text))
    assert await dispatch_pending(factory, matcher) == 0
    assert await rows(app, Delivery) == []


async def test_the_same_message_twice_is_one_dm(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory, **story_campaign(keywords=["link"]))
    await add_webhook_event(factory, story_reply_payload(shape="messaging"))
    await add_webhook_event(factory, story_reply_payload(shape="changes"))
    assert await dispatch_pending(factory, matcher) == 1
    assert len(await rows(app, Delivery)) == 1


async def test_own_account_and_echoes_never_trigger(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory, **story_campaign(keywords=["link"]))
    await add_webhook_event(factory, story_reply_payload(mid="a", sender_id=OWNER_ID))
    await add_webhook_event(factory, story_reply_payload(mid="b", is_echo=True))
    assert await dispatch_pending(factory, matcher) == 0
    assert await rows(app, Delivery) == []


async def test_campaign_triggers_do_not_cross(app: FastAPI) -> None:
    factory = app.state.session_factory
    # Answers comments only: a story reply with its keyword must not use it.
    await add_campaign(factory, keywords=["link"])
    await add_webhook_event(factory, story_reply_payload(text="link"))
    assert await dispatch_pending(factory, matcher) == 0
    # Answers story replies only: a comment with its keyword must not use it.
    await add_campaign(factory, **story_campaign(keywords=["promo"]))
    await add_webhook_event(factory, comment_payload(comment_id="c9", text="promo"))
    assert await dispatch_pending(factory, matcher) == 0


async def test_a_campaign_can_answer_both(app: FastAPI) -> None:
    factory = app.state.session_factory
    await add_campaign(factory, keywords=["link"], trigger_story_replies=True)
    await add_webhook_event(factory, comment_payload(comment_id="c1", text="link"))
    await add_webhook_event(factory, story_reply_payload(mid="m1", text="link"))
    assert await dispatch_pending(factory, matcher) == 2
    types = sorted(j.type for j in await rows(app, Job))
    assert types == [JobType.DIRECT_MESSAGE, JobType.PRIVATE_REPLY]


async def test_the_oldest_story_campaign_wins(app: FastAPI) -> None:
    factory = app.state.session_factory
    first = await add_campaign(factory, **story_campaign(name="first", keywords=["link"]))
    await add_campaign(factory, **story_campaign(name="second", keywords=["link"]))
    await add_webhook_event(factory, story_reply_payload())
    await dispatch_pending(factory, matcher)
    [delivery] = await rows(app, Delivery)
    assert delivery.campaign_id == first.id


# ---- worker --------------------------------------------------------------------------------


@pytest.fixture
async def meta() -> AsyncIterator[MetaClient]:
    async with httpx.AsyncClient() as http:
        yield MetaClient(http, "v25.0")


@pytest.fixture
async def ready(app: FastAPI, settings: Settings) -> FastAPI:
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
    await add_campaign(app.state.session_factory, **story_campaign(keywords=["link"]))
    return app


def make_worker(app: FastAPI, settings: Settings, meta: MetaClient, limit: int = 750) -> Worker:
    return Worker(
        app.state.session_factory, settings, meta, RateLimiter(app.state.session_factory, limit)
    )


async def story(app: FastAPI, mid: str = "mid_1") -> None:
    await add_webhook_event(app.state.session_factory, story_reply_payload(mid=mid))
    await dispatch_pending(app.state.session_factory, matcher)


@respx.mock
async def test_the_dm_goes_to_the_user_id_with_the_looked_up_username(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    lookup = respx.get(PROFILE).mock(return_value=httpx.Response(200, json={"username": "ana"}))
    send = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m_out"}))
    await story(ready)
    await make_worker(ready, settings, meta).run_until_idle()

    [delivery] = await rows(ready, Delivery)
    [job] = await rows(ready, Job)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.dm_message_id == "m_out"
    assert delivery.actor_username == "ana"
    assert job.status == JobStatus.DONE
    assert lookup.calls.last.request.url.params["fields"] == "username"
    body = json.loads(send.calls.last.request.content)
    assert body["recipient"] == {"id": FAN_ID}
    payload = body["message"]["attachment"]["payload"]
    assert payload["text"] == "Oi ana! Aqui está"
    assert payload["buttons"] == [
        {"type": "web_url", "url": "https://exemplo.com", "title": "Abrir"}
    ]


@respx.mock
async def test_the_dm_still_goes_out_when_the_username_cannot_be_looked_up(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(PROFILE).mock(return_value=httpx.Response(400, json={"error": {"code": 100}}))
    send = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m_out"}))
    await story(ready)
    await make_worker(ready, settings, meta).run_until_idle()

    [delivery] = await rows(ready, Delivery)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.actor_username is None
    text = json.loads(send.calls.last.request.content)["message"]["attachment"]["payload"]["text"]
    assert text == "Oi ! Aqui está"


@respx.mock
async def test_direct_messages_do_not_use_the_private_reply_limit(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(PROFILE).mock(return_value=httpx.Response(200, json={"username": "ana"}))
    respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m_out"}))
    async with ready.state.session_factory() as session, session.begin():
        session.add(SendEvent(kind="private_reply", job_id=0, sent_at=utcnow()))
    await story(ready)
    # The hourly limit is already used up by one private reply.
    await make_worker(ready, settings, meta, limit=1).run_until_idle()

    [delivery] = await rows(ready, Delivery)
    assert delivery.status == DeliveryStatus.SENT
    async with ready.state.session_factory() as session:
        used = (await session.execute(select(func.count(SendEvent.id)))).scalar_one()
    assert used == 1  # only the one we put there


@respx.mock
async def test_an_expired_window_fails_without_calling_meta(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    send = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    await story(ready)
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(update(Delivery).values(created_at=utcnow() - timedelta(hours=25)))
    await make_worker(ready, settings, meta).run_until_idle()

    [delivery] = await rows(ready, Delivery)
    [job] = await rows(ready, Job)
    assert not send.called
    assert delivery.status == DeliveryStatus.FAILED
    assert "24 h" in (delivery.error_message or "")
    assert job.status == JobStatus.FAILED


@respx.mock
async def test_a_timeout_is_unconfirmed_and_never_resent(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(PROFILE).mock(return_value=httpx.Response(200, json={"username": "ana"}))
    send = respx.post(SEND).mock(side_effect=httpx.ReadTimeout("slow"))
    await story(ready)
    worker = make_worker(ready, settings, meta)
    await worker.run_until_idle()
    await worker.run_until_idle()

    [delivery] = await rows(ready, Delivery)
    [job] = await rows(ready, Job)
    assert delivery.status == DeliveryStatus.UNCONFIRMED
    assert job.status == JobStatus.UNCONFIRMED
    assert send.call_count == 1


@respx.mock
async def test_a_refused_button_template_is_resent_once_as_text(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(PROFILE).mock(return_value=httpx.Response(200, json={"username": "ana"}))
    send = respx.post(SEND).mock(
        side_effect=[
            httpx.Response(400, json={"error": {"message": "bad template", "code": 100}}),
            httpx.Response(200, json={"message_id": "m_out"}),
        ]
    )
    await story(ready)
    await make_worker(ready, settings, meta).run_until_idle()

    [delivery] = await rows(ready, Delivery)
    assert delivery.status == DeliveryStatus.SENT
    last = json.loads(send.calls.last.request.content)["message"]
    assert last == {"text": "Oi ana! Aqui está\nhttps://exemplo.com"}


async def test_a_crash_during_a_direct_message_is_unconfirmed_for_the_dm(ready: FastAPI) -> None:
    await story(ready)
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(
            update(Job).values(status=JobStatus.RUNNING, send_started_at=utcnow())
        )
    await recover_stale(ready.state.session_factory, at_boot=True)

    [delivery] = await rows(ready, Delivery)
    assert delivery.status == DeliveryStatus.UNCONFIRMED
    assert delivery.public_reply_status is None
