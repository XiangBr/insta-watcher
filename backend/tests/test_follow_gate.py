"""Follow gate: an invite with a button first, the link only after Meta says they follow."""

import json
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
from app.dispatcher import (
    TapOutcome,
    dispatch_pending,
    enqueue_gate_tap,
    parse_postbacks,
)
from app.gate import CHECK_RETRY_DELAYS, MAX_TAPS, RECHECK_DELAYS, gate_payload
from app.maintenance import apply_retention
from app.matching import KeywordMatcher
from app.meta.client import GRAPH_HOST, MetaClient
from app.models import (
    Account,
    AccountStatus,
    Delivery,
    DeliveryStatus,
    GateState,
    Job,
    JobStatus,
    JobType,
    OpsEvent,
    PostbackReceipt,
    WebhookEvent,
    WebhookStatus,
)
from app.queue import claim_due, recover_stale
from app.ratelimit import RateLimiter
from app.tokens import cipher_for
from app.worker import Worker
from tests.factories import (
    FAN_ID,
    OWNER_ID,
    add_campaign,
    add_webhook_event,
    comment_payload,
    postback_webhook,
    story_reply_payload,
)

TOKEN = "IGAAgatetoken" + "g" * 40
SEND = f"{GRAPH_HOST}/v25.0/me/messages"
PROFILE = f"{GRAPH_HOST}/v25.0/{FAN_ID}"
LINK = "https://exemplo.com/ebook"
matcher = KeywordMatcher()


def gated(**overrides: Any) -> dict[str, Any]:
    """A campaign for story replies that holds its link behind the follow gate."""
    return {
        "keywords": ["link"],
        "post_id": None,
        "match_any_post": False,
        "trigger_comments": False,
        "trigger_story_replies": True,
        "dm_message": "Oi {username}! Aqui está",
        "link_url": LINK,
        "link_button_label": "Abrir",
        "follow_gate_enabled": True,
        "follow_gate_message": "Oi {username}! Segue a conta e toque no botão.",
        "follow_gate_button_label": "Já segui",
        **overrides,
    }


async def rows(app: FastAPI, model: Any) -> list[Any]:
    async with app.state.session_factory() as session:
        return list((await session.execute(select(model))).scalars().all())


async def delivery_of(app: FastAPI) -> Delivery:
    [delivery] = await rows(app, Delivery)
    return delivery


# ---- fixtures and helpers ------------------------------------------------------------------


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
    return app


def worker(app: FastAPI, settings: Settings, meta: MetaClient) -> Worker:
    return Worker(
        app.state.session_factory, settings, meta, RateLimiter(app.state.session_factory, 750)
    )


def profile_route(*answers: object, username: str = "ana") -> respx.Route:
    """Mocks the profile API. A username lookup always answers; each follow lookup takes the
    next answer, and the last one repeats. An answer is True, False, None, "absent" (the key
    is missing), an httpx.Response or an exception to raise."""
    pending = list(answers)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["fields"] == "username":
            return httpx.Response(200, json={"username": username})
        answer = pending.pop(0) if len(pending) > 1 else pending[0]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        if answer == "absent":
            return httpx.Response(200, json={"id": FAN_ID})
        return httpx.Response(200, json={"is_user_follow_business": answer})

    return respx.get(PROFILE).mock(side_effect=respond)


def follow_calls(route: respx.Route) -> int:
    return sum(1 for c in route.calls if c.request.url.params["fields"] != "username")


def sent(route: respx.Route) -> list[dict[str, Any]]:
    return [json.loads(call.request.content) for call in route.calls]


def ok_send(message_id: str = "m_out") -> httpx.Response:
    return httpx.Response(200, json={"message_id": message_id})


async def story_reply(app: FastAPI, mid: str = "mid_1", text: str = "quero o link") -> None:
    await add_webhook_event(app.state.session_factory, story_reply_payload(mid=mid, text=text))
    await dispatch_pending(app.state.session_factory, matcher)


async def tap(app: FastAPI, delivery_id: int, mid: str = "pb_1", sender: str = FAN_ID) -> None:
    await add_webhook_event(
        app.state.session_factory,
        postback_webhook(mid=mid, payload=gate_payload(delivery_id), sender_id=sender),
    )
    await dispatch_pending(app.state.session_factory, matcher)


async def make_due(app: FastAPI) -> None:
    """Let jobs that are waiting for a delay run now."""
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            update(Job).where(Job.status == JobStatus.PENDING).values(run_at=utcnow())
        )


async def invited(app: FastAPI, settings: Settings, meta: MetaClient) -> Delivery:
    """A story reply to a gated campaign, with the invite already sent."""
    await story_reply(app)
    await worker(app, settings, meta).run_until_idle()
    return await delivery_of(app)


# ---- the campaign options ------------------------------------------------------------------


@pytest.fixture
async def api(client: httpx.AsyncClient, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    client.headers["authorization"] = f"Bearer {settings.api_admin_token.get_secret_value()}"
    yield client


BODY: dict[str, Any] = {
    "name": "gated",
    "keywords": ["link"],
    "trigger_comments": False,
    "trigger_story_replies": True,
    "dm_message": "Aqui está",
    "link_url": LINK,
    "follow_gate_enabled": True,
    "follow_gate_message": "Segue a conta e toque no botão.",
}


async def test_the_gate_is_stored_with_a_default_button_label(api: httpx.AsyncClient) -> None:
    response = await api.post("/api/v1/campaigns", json=BODY)
    assert response.status_code == 201
    campaign = response.json()
    assert campaign["follow_gate_enabled"] is True
    assert campaign["follow_gate_message"] == "Segue a conta e toque no botão."
    assert campaign["follow_gate_button_label"] == "Já segui"


@pytest.mark.parametrize(
    ("changes", "needle"),
    [
        ({"link_url": None}, "link_url"),  # nothing to hold back
        ({"follow_gate_message": None}, "invite message"),
        ({"follow_gate_message": "   "}, "invite message"),
        ({"follow_gate_button_label": "x" * 21}, "20"),
        ({"follow_gate_message": "x" * 641}, "640"),
    ],
)
async def test_an_incomplete_gate_is_refused(
    api: httpx.AsyncClient, changes: dict[str, Any], needle: str
) -> None:
    response = await api.post("/api/v1/campaigns", json={**BODY, **changes})
    assert response.status_code == 422
    assert needle in response.text


async def test_switching_the_gate_off_clears_its_texts(api: httpx.AsyncClient) -> None:
    campaign = (await api.post("/api/v1/campaigns", json=BODY)).json()
    patched = await api.patch(
        f"/api/v1/campaigns/{campaign['id']}", json={"follow_gate_enabled": False}
    )
    assert patched.status_code == 200
    assert patched.json()["follow_gate_enabled"] is False
    assert patched.json()["follow_gate_message"] is None
    assert patched.json()["follow_gate_button_label"] is None


async def test_the_limits_include_the_invite_size(api: httpx.AsyncClient) -> None:
    assert (await api.get("/api/v1/limits")).json()["gate_message_max"] == 640


# ---- parsing a tap -------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["messaging", "changes"])
def test_a_tap_is_read_in_both_webhook_shapes(shape: str) -> None:
    [tap_event] = parse_postbacks(postback_webhook(payload="bw:gate:42", shape=shape))
    assert tap_event.delivery_id == 42
    assert tap_event.sender_id == FAN_ID
    assert tap_event.mid == "pb_1"


@pytest.mark.parametrize(
    "payload",
    [
        "bw:probe:7",  # the diagnostics probe
        "bw:gate:",
        "bw:gate:abc",
        "bw:gate:12abc",
        "bw:gate:-3",
        "bw:gate:1234567890123",  # absurdly long
        " bw:gate:5",
        "other",
        "",
        None,
    ],
)
def test_only_our_own_button_format_counts(payload: str | None) -> None:
    assert list(parse_postbacks(postback_webhook(payload=payload))) == []


def test_other_events_are_not_taps() -> None:
    assert list(parse_postbacks(story_reply_payload())) == []
    assert list(parse_postbacks(comment_payload())) == []
    assert list(parse_postbacks(postback_webhook(mid=None))) == []


# ---- the invite ----------------------------------------------------------------------------


@respx.mock
async def test_the_invite_carries_a_postback_button_and_never_the_link(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    route = respx.post(SEND).mock(return_value=ok_send())
    delivery = await invited(ready, settings, meta)

    [body] = sent(route)
    assert body["recipient"] == {"id": FAN_ID}
    payload = body["message"]["attachment"]["payload"]
    assert payload["text"] == "Oi ana! Segue a conta e toque no botão."
    assert payload["buttons"] == [
        {"type": "postback", "title": "Já segui", "payload": f"bw:gate:{delivery.id}"}
    ]
    assert LINK not in json.dumps(body)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.gate_state == GateState.WAITING
    assert delivery.followup_status is None


@respx.mock
async def test_a_comment_invite_is_a_private_reply_with_the_same_button(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(
        ready.state.session_factory,
        **gated(trigger_comments=True, trigger_story_replies=False, match_any_post=True),
    )
    route = respx.post(SEND).mock(return_value=ok_send())
    await add_webhook_event(
        ready.state.session_factory, comment_payload(text="quero o link", username="bia")
    )
    await dispatch_pending(ready.state.session_factory, matcher)
    await worker(ready, settings, meta).run_until_idle()

    [body] = sent(route)
    assert body["recipient"] == {"comment_id": "c_1"}
    button = body["message"]["attachment"]["payload"]["buttons"][0]
    assert button["type"] == "postback"
    assert LINK not in json.dumps(body)
    assert (await delivery_of(ready)).gate_state == GateState.WAITING


@respx.mock
async def test_a_refused_postback_fails_loudly_and_does_not_leak_the_link_as_text(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    route = respx.post(SEND).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad template", "code": 100}})
    )
    await story_reply(ready)
    await worker(ready, settings, meta).run_until_idle()

    delivery = await delivery_of(ready)
    assert delivery.status == DeliveryStatus.FAILED
    assert route.call_count == 1  # the text-only retry would have dropped the button
    assert LINK not in json.dumps(sent(route))


@respx.mock
async def test_a_gate_switched_off_later_sends_the_link_straight_away(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    campaign = await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    route = respx.post(SEND).mock(return_value=ok_send())
    await story_reply(ready)
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(
            update(type(campaign)).values(follow_gate_enabled=False, follow_gate_message=None)
        )
    await worker(ready, settings, meta).run_until_idle()

    [body] = sent(route)
    assert body["message"]["attachment"]["payload"]["buttons"][0]["type"] == "web_url"
    delivery = await delivery_of(ready)
    assert delivery.gate_state is None
    assert delivery.status == DeliveryStatus.SENT


@respx.mock
async def test_campaigns_without_a_gate_are_untouched(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(
        ready.state.session_factory, **gated(follow_gate_enabled=False, follow_gate_message=None)
    )
    profile_route(True)
    route = respx.post(SEND).mock(return_value=ok_send())
    await story_reply(ready)
    await worker(ready, settings, meta).run_until_idle()

    delivery = await delivery_of(ready)
    assert delivery.gate_state is None
    assert sent(route)[0]["message"]["attachment"]["payload"]["buttons"][0]["type"] == "web_url"


# ---- the tap ------------------------------------------------------------------------------


@respx.mock
async def test_a_tap_queues_one_check_and_remembers_who_tapped(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    respx.post(SEND).mock(return_value=ok_send())
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)

    delivery = await delivery_of(ready)
    [receipt] = await rows(ready, PostbackReceipt)
    checks = [j for j in await rows(ready, Job) if j.type == JobType.FOLLOW_CHECK]
    assert receipt.mid == "pb_1"
    assert delivery.gate_state == GateState.CHECKING
    assert delivery.gate_user_id == FAN_ID
    assert delivery.gate_taps == 1
    assert delivery.gate_tapped_at is not None
    assert len(checks) == 1
    assert checks[0].payload == {"check": 1}


@respx.mock
async def test_the_same_tap_delivered_twice_counts_once(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    respx.post(SEND).mock(return_value=ok_send())
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id, mid="pb_dup")
    await tap(ready, delivery.id, mid="pb_dup")

    assert (await delivery_of(ready)).gate_taps == 1
    assert len([j for j in await rows(ready, Job) if j.type == JobType.FOLLOW_CHECK]) == 1
    # A replay is recognised and skipped. It must not be left to a database error to stop it,
    # which would also mark the webhook event as failed.
    statuses = {e.status for e in await rows(ready, WebhookEvent)}
    assert statuses == {WebhookStatus.PROCESSED}


async def enqueue(app: FastAPI, **kwargs: Any) -> TapOutcome:
    [one] = parse_postbacks(postback_webhook(**kwargs))
    async with app.state.session_factory() as session, session.begin():
        return await enqueue_gate_tap(session, one, account_ig_id=OWNER_ID)


@respx.mock
async def test_taps_that_must_not_count(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    respx.post(SEND).mock(return_value=ok_send())
    delivery = await invited(ready, settings, meta)

    assert await enqueue(ready, payload=gate_payload(99999), mid="a") == TapOutcome.UNKNOWN_DELIVERY
    assert (
        await enqueue(ready, payload=gate_payload(delivery.id), sender_id="someone_else", mid="b")
        == TapOutcome.WRONG_USER
    )
    assert (
        await enqueue(ready, payload=gate_payload(delivery.id), sender_id=OWNER_ID, mid="c")
        == TapOutcome.OWN_ACCOUNT
    )
    assert (await delivery_of(ready)).gate_taps == 0
    assert [j for j in await rows(ready, Job) if j.type == JobType.FOLLOW_CHECK] == []


async def test_a_tap_on_a_delivery_without_a_gate_is_ignored(app: FastAPI) -> None:
    await add_campaign(
        app.state.session_factory, **gated(follow_gate_enabled=False, follow_gate_message=None)
    )
    await story_reply(app)
    delivery = await delivery_of(app)
    assert await enqueue(app, payload=gate_payload(delivery.id)) == TapOutcome.NOT_GATED


async def test_the_person_of_a_comment_is_learned_from_the_first_tap(app: FastAPI) -> None:
    await add_campaign(
        app.state.session_factory,
        **gated(trigger_comments=True, trigger_story_replies=False, match_any_post=True),
    )
    await add_webhook_event(app.state.session_factory, comment_payload(text="quero o link"))
    await dispatch_pending(app.state.session_factory, matcher)
    delivery = await delivery_of(app)

    first = await enqueue(app, payload=gate_payload(delivery.id), sender_id="igsid_1", mid="a")
    other = await enqueue(app, payload=gate_payload(delivery.id), sender_id="igsid_2", mid="b")
    again = await enqueue(app, payload=gate_payload(delivery.id), sender_id="igsid_1", mid="c")
    assert (first, other, again) == (TapOutcome.QUEUED, TapOutcome.WRONG_USER, TapOutcome.QUEUED)
    assert (await delivery_of(app)).gate_user_id == "igsid_1"


async def test_a_person_can_only_tap_so_many_times(app: FastAPI) -> None:
    await add_campaign(app.state.session_factory, **gated())
    await story_reply(app)
    delivery = await delivery_of(app)
    outcomes = [
        await enqueue(app, payload=gate_payload(delivery.id), mid=f"pb_{i}")
        for i in range(MAX_TAPS + 2)
    ]
    assert outcomes[:MAX_TAPS] == [TapOutcome.QUEUED] * MAX_TAPS
    assert outcomes[MAX_TAPS:] == [TapOutcome.TOO_MANY] * 2
    assert (await delivery_of(app)).gate_state == GateState.STOPPED


# ---- the check -----------------------------------------------------------------------------


async def run_all(app: FastAPI, settings: Settings, meta: MetaClient) -> None:
    await worker(app, settings, meta).run_until_idle()


@respx.mock
async def test_someone_who_follows_gets_the_link(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile = profile_route(True)
    route = respx.post(SEND).mock(side_effect=[ok_send("m_invite"), ok_send("m_link")])
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    assert delivery.gate_state == GateState.PASSED
    assert delivery.followup_status == DeliveryStatus.SENT
    assert delivery.followup_message_id == "m_link"
    assert delivery.followup_sent_at is not None
    assert follow_calls(profile) == 1
    invite, link = sent(route)
    assert link["recipient"] == {"id": FAN_ID}
    payload = link["message"]["attachment"]["payload"]
    assert payload["text"] == "Oi ana! Aqui está"
    assert payload["buttons"] == [{"type": "web_url", "url": LINK, "title": "Abrir"}]
    assert LINK not in json.dumps(invite)


@respx.mock
async def test_the_follow_lookup_asks_only_for_the_follow_field(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile = profile_route(True)
    respx.post(SEND).mock(return_value=ok_send())
    delivery = await invited(ready, settings, meta)
    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)

    lookups = [c.request for c in profile.calls if c.request.url.params["fields"] != "username"]
    assert [r.url.params["fields"] for r in lookups] == ["is_user_follow_business"]
    assert lookups[0].headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
async def test_not_following_yet_waits_and_looks_again(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile = profile_route(False, True)  # still not on the first look, following on the second
    route = respx.post(SEND).mock(side_effect=[ok_send("m_invite"), ok_send("m_link")])
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)

    # The first look found nothing: no link yet, and a second look is waiting.
    delivery = await delivery_of(ready)
    assert delivery.gate_state == GateState.CHECKING
    assert delivery.followup_status is None
    [second] = [
        j
        for j in await rows(ready, Job)
        if j.type == JobType.FOLLOW_CHECK and j.status == "PENDING"
    ]
    assert second.payload == {"check": 2}
    assert second.run_at > utcnow() + RECHECK_DELAYS[0] - timedelta(seconds=5)
    assert route.call_count == 1

    await make_due(ready)
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    assert delivery.gate_state == GateState.PASSED
    assert delivery.followup_status == DeliveryStatus.SENT
    assert follow_calls(profile) == 2
    assert route.call_count == 2


@respx.mock
async def test_still_not_following_after_the_last_look_asks_again_and_waits_for_a_tap(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile = profile_route(False)
    route = respx.post(SEND).mock(return_value=ok_send())
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    for _ in range(3):  # three looks: now, +20 s, +40 s
        await run_all(ready, settings, meta)
        await make_due(ready)
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    assert follow_calls(profile) == 3
    assert delivery.gate_state == GateState.REMINDED
    assert delivery.followup_status is None
    # The invite and one reminder, with the same button and never the link.
    invite, reminder = sent(route)
    assert reminder["recipient"] == {"id": FAN_ID}
    assert reminder["message"]["attachment"]["payload"]["buttons"][0]["type"] == "postback"
    assert LINK not in json.dumps(reminder)
    assert LINK not in json.dumps(invite)

    # Tapping again starts a new round, and this time they follow.
    profile_route(True)
    respx.post(SEND).mock(return_value=ok_send("m_link"))
    await tap(ready, delivery.id, mid="pb_2")
    await run_all(ready, settings, meta)
    delivery = await delivery_of(ready)
    assert delivery.gate_state == GateState.PASSED
    assert delivery.followup_status == DeliveryStatus.SENT
    assert delivery.gate_taps == 2


@respx.mock
@pytest.mark.parametrize("answer", [None, "absent"])
async def test_when_meta_cannot_tell_the_link_is_released(
    ready: FastAPI, settings: Settings, meta: MetaClient, answer: object
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(answer)
    route = respx.post(SEND).mock(side_effect=[ok_send("m_invite"), ok_send("m_link")])
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    assert delivery.gate_state == GateState.RELEASED
    assert delivery.followup_status == DeliveryStatus.SENT
    assert route.call_count == 2
    notes = [e.message for e in await rows(ready, OpsEvent)]
    assert any("unknown, link released" in m for m in notes)


@respx.mock
async def test_a_refusal_from_meta_counts_as_cannot_tell(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(
        httpx.Response(400, json={"error": {"message": "No consent for this person", "code": 230}})
    )
    respx.post(SEND).mock(side_effect=[ok_send("m_invite"), ok_send("m_link")])
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    assert delivery.gate_state == GateState.RELEASED
    assert delivery.followup_status == DeliveryStatus.SENT


@respx.mock
async def test_a_failed_lookup_is_retried_before_anything_is_released(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile = profile_route(httpx.ReadTimeout("slow"), True)
    route = respx.post(SEND).mock(side_effect=[ok_send("m_invite"), ok_send("m_link")])
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)

    [job] = [j for j in await rows(ready, Job) if j.type == JobType.FOLLOW_CHECK]
    delivery = await delivery_of(ready)
    assert job.status == JobStatus.PENDING
    assert job.run_at > utcnow() + CHECK_RETRY_DELAYS[0] - timedelta(seconds=5)
    assert delivery.followup_status is None  # a failed lookup is not a green light
    assert route.call_count == 1

    await make_due(ready)
    await run_all(ready, settings, meta)
    delivery = await delivery_of(ready)
    assert delivery.gate_state == GateState.PASSED
    assert follow_calls(profile) == 2


@respx.mock
async def test_a_lookup_that_never_works_finally_releases_the_link(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(httpx.ReadTimeout("slow"))
    respx.post(SEND).mock(side_effect=[ok_send("m_invite"), ok_send("m_link")])
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    for _ in range(len(CHECK_RETRY_DELAYS) + 1):
        await run_all(ready, settings, meta)
        await make_due(ready)
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    assert delivery.gate_state == GateState.RELEASED
    assert delivery.followup_status == DeliveryStatus.SENT


@respx.mock
async def test_a_rejected_token_pauses_the_check_instead_of_deciding(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(httpx.Response(401, json={"error": {"message": "Invalid token", "code": 190}}))
    respx.post(SEND).mock(return_value=ok_send())
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    [account] = await rows(ready, Account)
    [job] = [j for j in await rows(ready, Job) if j.type == JobType.FOLLOW_CHECK]
    assert account.status == AccountStatus.TOKEN_INVALID
    assert delivery.followup_status is None
    assert delivery.gate_state == GateState.CHECKING
    assert job.status == JobStatus.PENDING
    assert job.attempts == 0  # the attempt is given back: the token was not the person's fault


@respx.mock
async def test_two_taps_that_both_find_a_follower_send_the_link_once(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    route = respx.post(SEND).mock(side_effect=[ok_send("m_invite"), ok_send("m_link")])
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id, mid="pb_1")
    await tap(ready, delivery.id, mid="pb_2")  # impatient: tapped again before the first ran
    await run_all(ready, settings, meta)

    followups = [j for j in await rows(ready, Job) if j.type == JobType.FOLLOWUP_DM]
    assert len(followups) == 1
    assert route.call_count == 2  # the invite and one link
    assert (await delivery_of(ready)).followup_status == DeliveryStatus.SENT


@respx.mock
async def test_two_checks_that_race_still_queue_the_link_once(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    # Both checks are claimed and read the delivery before either has released the link, which
    # is what two workers at the same moment would do. The second must not queue a second link.
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    respx.post(SEND).mock(return_value=ok_send())
    delivery = await invited(ready, settings, meta)
    await tap(ready, delivery.id, mid="pb_1")
    await tap(ready, delivery.id, mid="pb_2")

    w = worker(ready, settings, meta)
    claimed = await claim_due(ready.state.session_factory, 3)
    contexts = [await w._load(job_id) for job_id in claimed]
    assert len(contexts) == 2
    for ctx in contexts:
        assert ctx is not None
        await w._follow_check(ctx)

    followups = [j for j in await rows(ready, Job) if j.type == JobType.FOLLOWUP_DM]
    assert len(followups) == 1


@respx.mock
async def test_a_tap_after_the_link_went_out_does_nothing(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    route = respx.post(SEND).mock(side_effect=[ok_send("m_invite"), ok_send("m_link")])
    delivery = await invited(ready, settings, meta)
    await tap(ready, delivery.id, mid="pb_1")
    await run_all(ready, settings, meta)

    await tap(ready, delivery.id, mid="pb_late")
    await run_all(ready, settings, meta)

    assert route.call_count == 2
    assert (await delivery_of(ready)).gate_taps == 1


# ---- the link message ----------------------------------------------------------------------


@respx.mock
async def test_the_link_is_not_sent_after_the_window_closed(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    route = respx.post(SEND).mock(return_value=ok_send())
    delivery = await invited(ready, settings, meta)
    await tap(ready, delivery.id)
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(
            update(Delivery).values(gate_tapped_at=utcnow() - timedelta(hours=25))
        )
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    assert delivery.followup_status == DeliveryStatus.FAILED
    assert "24 h" in (delivery.followup_error or "")
    assert route.call_count == 1  # only the invite


@respx.mock
async def test_a_timeout_on_the_link_is_unconfirmed_and_never_resent(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    route = respx.post(SEND).mock(side_effect=[ok_send("m_invite"), httpx.ReadTimeout("slow")])
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)
    await make_due(ready)
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    assert delivery.followup_status == DeliveryStatus.UNCONFIRMED
    assert delivery.status == DeliveryStatus.SENT  # the invite is not affected
    assert route.call_count == 2


@respx.mock
async def test_a_refused_link_button_is_resent_once_as_text(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    route = respx.post(SEND).mock(
        side_effect=[
            ok_send("m_invite"),
            httpx.Response(400, json={"error": {"message": "bad template", "code": 100}}),
            ok_send("m_link"),
        ]
    )
    delivery = await invited(ready, settings, meta)

    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)

    delivery = await delivery_of(ready)
    assert delivery.followup_status == DeliveryStatus.SENT
    last = sent(route)[-1]["message"]
    assert last == {"text": f"Oi ana! Aqui está\n{LINK}"}


@respx.mock
async def test_a_failed_reminder_is_noted_and_does_not_stop_the_flow(
    ready: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(False)
    respx.post(SEND).mock(
        side_effect=[
            ok_send("m_invite"),
            httpx.Response(400, json={"error": {"message": "nope", "code": 10}}),
        ]
    )
    delivery = await invited(ready, settings, meta)
    await tap(ready, delivery.id)
    for _ in range(3):
        await run_all(ready, settings, meta)
        await make_due(ready)
    await run_all(ready, settings, meta)

    assert (await delivery_of(ready)).gate_state == GateState.REMINDED
    notes = [e.message for e in await rows(ready, OpsEvent)]
    assert any("reminder" in m and "not sent" in m for m in notes)


# ---- crashes -------------------------------------------------------------------------------


async def test_a_crash_while_sending_the_link_is_unconfirmed_for_the_link(
    ready: FastAPI,
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    await story_reply(ready)
    delivery = await delivery_of(ready)
    async with ready.state.session_factory() as session, session.begin():
        session.add(
            Job(
                type=JobType.FOLLOWUP_DM,
                delivery_id=delivery.id,
                dedupe_key=f"followup:{delivery.id}",
                status=JobStatus.RUNNING,
                send_started_at=utcnow(),
            )
        )
    await recover_stale(ready.state.session_factory, at_boot=True)

    delivery = await delivery_of(ready)
    assert delivery.followup_status == DeliveryStatus.UNCONFIRMED
    assert delivery.status == DeliveryStatus.PENDING  # the invite is untouched


async def test_a_crash_during_a_check_just_runs_the_check_again(ready: FastAPI) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    await story_reply(ready)
    delivery = await delivery_of(ready)
    await tap(ready, delivery.id)
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(
            update(Job).where(Job.type == JobType.FOLLOW_CHECK).values(status=JobStatus.RUNNING)
        )
    await recover_stale(ready.state.session_factory, at_boot=True)

    [job] = [j for j in await rows(ready, Job) if j.type == JobType.FOLLOW_CHECK]
    assert job.status == JobStatus.PENDING  # a read-only check is safe to repeat


# ---- retention -----------------------------------------------------------------------------


async def test_old_receipts_and_the_person_of_old_deliveries_are_dropped(
    ready: FastAPI, settings: Settings
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    await story_reply(ready)
    delivery = await delivery_of(ready)
    await tap(ready, delivery.id)
    old = utcnow() - timedelta(days=settings.retention_delivery_days + 1)
    async with ready.state.session_factory() as session, session.begin():
        await session.execute(update(Delivery).values(created_at=old))
        await session.execute(
            update(PostbackReceipt).values(
                created_at=utcnow() - timedelta(days=settings.retention_webhook_days + 1)
            )
        )

    result = await apply_retention(ready.state.session_factory, settings)

    assert result.postback_receipts_deleted == 1
    assert await rows(ready, PostbackReceipt) == []
    delivery = await delivery_of(ready)
    assert delivery.gate_user_id is None
    assert delivery.actor_id == "anon"


# ---- what the dashboard sees ---------------------------------------------------------------


@respx.mock
async def test_the_delivery_api_shows_where_the_gate_stands(
    ready: FastAPI, settings: Settings, meta: MetaClient, api: httpx.AsyncClient
) -> None:
    await add_campaign(ready.state.session_factory, **gated())
    profile_route(True)
    respx.post(SEND).mock(side_effect=[ok_send("m_invite"), ok_send("m_link")])
    delivery = await invited(ready, settings, meta)
    before = (await api.get("/api/v1/deliveries")).json()["items"][0]
    assert (before["gate_state"], before["followup_status"]) == ("WAITING", None)

    await tap(ready, delivery.id)
    await run_all(ready, settings, meta)
    after = (await api.get("/api/v1/deliveries")).json()["items"][0]
    assert after["gate_state"] == "PASSED"
    assert after["followup_status"] == "SENT"
    assert after["gate_taps"] == 1
    assert after["followup_error"] is None
