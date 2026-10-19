from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.meta.client import GRAPH_HOST
from app.models import (
    Account,
    Delivery,
    DeliveryStatus,
    Job,
    JobStatus,
    JobType,
    OpsEvent,
    Origin,
    WebhookEvent,
)
from app.tokens import TokenCipher
from tests.factories import POST_ID, add_campaign, comment_payload

TOKEN = "IGAAplaintexttoken" + "y" * 40
ME = f"{GRAPH_HOST}/v25.0/me"
MEDIA = f"{GRAPH_HOST}/v25.0/me/media"
SUBSCRIBE = f"{GRAPH_HOST}/v25.0/me/subscribed_apps"

CAMPAIGN: dict[str, Any] = {
    "name": "ebook",
    "keywords": ["ebook", "pdf"],
    "post_id": POST_ID,
    "dm_message": "Oi {username}!",
    "link_url": "https://exemplo.com/e",
    "link_button_label": "Baixar",
}


@pytest.fixture
async def api(client: httpx.AsyncClient, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    client.headers["authorization"] = f"Bearer {settings.api_admin_token.get_secret_value()}"
    yield client


@pytest.fixture
def sf(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    return factory


async def add_account(sf: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    from app.db import utcnow

    blob = TokenCipher(settings.encryption_key.get_secret_value()).encrypt(TOKEN)
    async with sf() as session, session.begin():
        session.add(
            Account(
                id=1,
                ig_user_id="17841400000000001",
                username="dono",
                token_encrypted=blob,
                token_set_at=utcnow(),
            )
        )


async def add_delivery(
    sf: async_sessionmaker[AsyncSession],
    campaign_id: int,
    source_id: str,
    status: DeliveryStatus,
    job_status: JobStatus = JobStatus.DONE,
) -> int:
    async with sf() as session, session.begin():
        delivery = Delivery(
            campaign_id=campaign_id,
            origin=Origin.WEBHOOK,
            source_id=source_id,
            actor_id="1",
            actor_username="fan",
            text="quero o ebook",
            matched_keyword="ebook",
            status=status,
        )
        session.add(delivery)
        await session.flush()
        session.add(
            Job(
                type=JobType.PRIVATE_REPLY,
                delivery_id=delivery.id,
                dedupe_key=f"private:{delivery.id}",
                status=job_status,
                attempts=3,
            )
        )
        return delivery.id


# ---- auth and error envelope -------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/account"),
        ("GET", "/api/v1/campaigns"),
        ("POST", "/api/v1/campaigns"),
        ("GET", "/api/v1/deliveries"),
        ("POST", "/api/v1/deliveries/1/retry"),
        ("POST", "/api/v1/reconcile"),
        ("POST", "/api/v1/login-throttle/check"),
        ("PUT", "/api/v1/account/token"),
    ],
)
async def test_every_admin_route_needs_the_token(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, headers={"authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_validation_errors_never_echo_the_input(api: httpx.AsyncClient) -> None:
    secret = "IGAA" + "z" * 4  # too short, rejected by validation
    response = await api.put("/api/v1/account/token", json={"token": secret})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert secret not in response.text


async def test_webhook_keeps_plain_errors(client: httpx.AsyncClient) -> None:
    response = await client.post("/webhook", content=b"{}")
    assert response.status_code == 401
    assert "error" not in response.json()


# ---- account -----------------------------------------------------------------------------


async def test_account_is_null_before_a_token(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/v1/account")
    assert response.status_code == 200
    assert response.json() is None


@respx.mock
async def test_put_token_stores_it_and_never_returns_it(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    respx.get(ME).mock(
        return_value=httpx.Response(200, json={"user_id": "17841400000000001", "username": "dono"})
    )
    response = await api.put("/api/v1/account/token", json={"token": f"  {TOKEN}\n"})
    assert response.status_code == 200
    assert response.json()["username"] == "dono"
    assert TOKEN not in response.text
    assert "token_encrypted" not in response.text
    async with sf() as session:
        account = await session.get(Account, 1)
    assert account is not None and TOKEN.encode() not in account.token_encrypted


@respx.mock
async def test_put_token_refused_by_meta_is_422(api: httpx.AsyncClient) -> None:
    respx.get(ME).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Invalid OAuth access token", "code": 190}}
        )
    )
    response = await api.put("/api/v1/account/token", json={"token": TOKEN})
    assert response.status_code == 422
    assert TOKEN not in response.text


async def test_pause_and_resume_need_an_account(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    assert (await api.post("/api/v1/account/pause")).status_code == 409
    await add_account(sf, settings)
    assert (await api.post("/api/v1/account/pause")).json()["sending_paused"] is True
    assert (await api.post("/api/v1/account/resume")).json()["sending_paused"] is False


@respx.mock
async def test_subscribe_records_the_time(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await add_account(sf, settings)
    route = respx.post(SUBSCRIBE).mock(return_value=httpx.Response(200, json={"success": True}))
    response = await api.post("/api/v1/account/subscribe")
    assert response.status_code == 200
    assert response.json()["webhook_subscribed_at"] is not None
    assert route.calls[0].request.headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
async def test_posts_lists_media_and_meta_failure_is_502(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    assert (await api.get("/api/v1/posts")).status_code == 409  # no token yet
    await add_account(sf, settings)
    respx.get(MEDIA).mock(
        side_effect=[
            httpx.Response(
                200, json={"data": [{"id": POST_ID, "caption": "oi", "media_type": "IMAGE"}]}
            ),
            httpx.Response(500, json={"error": {"message": "boom", "code": 2}}),
        ]
    )
    posts = (await api.get("/api/v1/posts")).json()
    assert posts == [
        {
            "id": POST_ID,
            "caption": "oi",
            "media_type": "IMAGE",
            "timestamp": None,
            "permalink": None,
            "media_url": None,
            "thumbnail_url": None,
        }
    ]
    failed = await api.get("/api/v1/posts")
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "upstream_error"


# ---- campaigns ---------------------------------------------------------------------------


async def test_campaign_crud_duplicate_and_counts(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    created = await api.post("/api/v1/campaigns", json=CAMPAIGN)
    assert created.status_code == 201
    campaign = created.json()
    assert campaign["is_active"] is True
    assert campaign["deliveries"] == {"pending": 0, "sent": 0, "failed": 0, "unconfirmed": 0}

    await add_delivery(sf, campaign["id"], "c1", DeliveryStatus.SENT)
    await add_delivery(sf, campaign["id"], "c2", DeliveryStatus.FAILED)
    listed = (await api.get("/api/v1/campaigns")).json()
    assert listed[0]["deliveries"]["sent"] == 1
    assert listed[0]["deliveries"]["failed"] == 1

    patched = await api.patch(
        f"/api/v1/campaigns/{campaign['id']}", json={"post_id": None, "match_any_post": True}
    )
    assert patched.status_code == 200
    assert patched.json()["match_any_post"] is True
    assert patched.json()["keywords"] == ["ebook", "pdf"]  # untouched fields stay

    paused = await api.post(f"/api/v1/campaigns/{campaign['id']}/pause")
    assert paused.json()["is_active"] is False
    resumed = await api.post(f"/api/v1/campaigns/{campaign['id']}/resume")
    assert resumed.json()["is_active"] is True

    copy = await api.post(f"/api/v1/campaigns/{campaign['id']}/duplicate")
    assert copy.status_code == 201
    assert copy.json()["name"] == "ebook (copy)"
    assert copy.json()["is_active"] is False
    assert copy.json()["deliveries"]["sent"] == 0

    assert (await api.delete(f"/api/v1/campaigns/{campaign['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/campaigns/{campaign['id']}")).status_code == 404
    assert [c["id"] for c in (await api.get("/api/v1/campaigns")).json()] == [copy.json()["id"]]


@pytest.mark.parametrize(
    "changes",
    [
        {"link_url": "http://inseguro.com"},
        {"link_button_label": "um rótulo comprido demais"},
        {"match_any_post": True},  # post_id still set: both targets
        {"keywords": ["🎉"]},
        {"unknown": 1},
    ],
)
async def test_invalid_patch_is_422_and_changes_nothing(
    api: httpx.AsyncClient, changes: dict[str, Any]
) -> None:
    campaign = (await api.post("/api/v1/campaigns", json=CAMPAIGN)).json()
    response = await api.patch(f"/api/v1/campaigns/{campaign['id']}", json=changes)
    assert response.status_code == 422
    assert response.json()["error"]["details"]
    after = (await api.get(f"/api/v1/campaigns/{campaign['id']}")).json()
    assert after["updated_at"] == campaign["updated_at"]
    assert after["link_url"] == CAMPAIGN["link_url"]


async def test_invalid_campaign_is_422(api: httpx.AsyncClient) -> None:
    response = await api.post("/api/v1/campaigns", json={**CAMPAIGN, "match_any_post": True})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


async def test_campaign_stats_per_day(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    campaign = (await api.post("/api/v1/campaigns", json=CAMPAIGN)).json()
    await add_delivery(sf, campaign["id"], "c1", DeliveryStatus.SENT)
    await add_delivery(sf, campaign["id"], "c2", DeliveryStatus.SENT)
    await add_delivery(sf, campaign["id"], "c3", DeliveryStatus.UNCONFIRMED)
    stats = (await api.get(f"/api/v1/campaigns/{campaign['id']}/stats")).json()
    assert len(stats["days"]) == 1
    assert stats["days"][0]["sent"] == 2
    assert stats["days"][0]["unconfirmed"] == 1
    assert len(stats["days"][0]["day"]) == 10
    assert stats["totals"]["sent"] == 2


# ---- deliveries --------------------------------------------------------------------------


async def test_deliveries_filter_and_paginate(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    campaign = await add_campaign(sf)
    for i in range(5):
        status = DeliveryStatus.FAILED if i == 2 else DeliveryStatus.SENT
        await add_delivery(sf, campaign.id, f"c{i}", status)

    first = (await api.get("/api/v1/deliveries", params={"limit": 2})).json()
    assert [d["id"] for d in first["items"]] == [5, 4]
    assert first["items"][0]["campaign_name"] == "link"
    second = (
        await api.get("/api/v1/deliveries", params={"limit": 2, "before": first["next_cursor"]})
    ).json()
    assert [d["id"] for d in second["items"]] == [3, 2]
    last = (
        await api.get("/api/v1/deliveries", params={"limit": 2, "before": second["next_cursor"]})
    ).json()
    assert [d["id"] for d in last["items"]] == [1]
    assert last["next_cursor"] is None

    failed = (await api.get("/api/v1/deliveries", params={"status": "FAILED"})).json()
    assert [d["id"] for d in failed["items"]] == [3]
    future = (await api.get("/api/v1/deliveries", params={"since": "2999-01-01T00:00:00Z"})).json()
    assert future["items"] == []
    naive = await api.get("/api/v1/deliveries", params={"since": "2026-01-01T00:00:00"})
    assert naive.status_code == 422


async def test_retry_failed_requeues_the_dm(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    campaign = await add_campaign(sf)
    delivery_id = await add_delivery(
        sf, campaign.id, "c1", DeliveryStatus.FAILED, job_status=JobStatus.FAILED
    )
    response = await api.post(f"/api/v1/deliveries/{delivery_id}/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"
    async with sf() as session:
        job = (await session.execute(select(Job))).scalar_one()
        events = (await session.execute(select(OpsEvent))).scalars().all()
    assert job.status == JobStatus.PENDING
    assert job.attempts == 0
    assert any("requeued from FAILED" in e.message for e in events)

    again = await api.post(f"/api/v1/deliveries/{delivery_id}/retry")
    assert again.status_code == 409  # already PENDING


async def test_retry_unconfirmed_needs_explicit_confirmation(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    campaign = await add_campaign(sf)
    delivery_id = await add_delivery(
        sf, campaign.id, "c1", DeliveryStatus.UNCONFIRMED, job_status=JobStatus.UNCONFIRMED
    )
    url = f"/api/v1/deliveries/{delivery_id}/retry"
    assert (await api.post(url)).status_code == 422
    assert (await api.post(url, json={"confirm_not_delivered": False})).status_code == 422
    async with sf() as session:
        job = (await session.execute(select(Job))).scalar_one()
    assert job.status == JobStatus.UNCONFIRMED  # nothing changed

    confirmed = await api.post(url, json={"confirm_not_delivered": True})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "PENDING"


async def test_sent_delivery_cannot_be_retried(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    campaign = await add_campaign(sf)
    delivery_id = await add_delivery(sf, campaign.id, "c1", DeliveryStatus.SENT)
    response = await api.post(
        f"/api/v1/deliveries/{delivery_id}/retry", json={"confirm_not_delivered": True}
    )
    assert response.status_code == 409
    assert (await api.post("/api/v1/deliveries/999/retry")).status_code == 404


# ---- operation ---------------------------------------------------------------------------


async def test_ops_events_resolve_drops_the_status_count(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    async with sf() as session, session.begin():
        session.add(OpsEvent(source="TOKEN", level="ERROR", message="token refresh failed"))
    assert (await api.get("/api/v1/status")).json()["unresolved_errors"] == 1
    events = (await api.get("/api/v1/ops-events", params={"unresolved": True})).json()
    resolved = await api.post(f"/api/v1/ops-events/{events[0]['id']}/resolve")
    assert resolved.json()["resolved_at"] is not None
    assert (await api.get("/api/v1/status")).json()["unresolved_errors"] == 0


async def test_webhook_events_hide_comment_text_by_default(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    async with sf() as session, session.begin():
        session.add(WebhookEvent(payload=comment_payload(text="meu segredo")))
    plain = await api.get("/api/v1/webhook-events")
    assert plain.json()[0]["changes"][0]["comment_id"] == "c_1"
    assert "meu segredo" not in plain.text
    with_text = await api.get("/api/v1/webhook-events", params={"with_text": True})
    assert with_text.json()[0]["changes"][0]["text"] == "meu segredo"


async def test_reconcile_needs_an_active_account(api: httpx.AsyncClient) -> None:
    response = await api.post("/api/v1/reconcile")
    assert response.status_code == 409


async def test_reconcile_runs_a_sweep(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await add_account(sf, settings)
    # No active campaign: the sweep has nothing to poll and touches no Meta endpoint.
    response = await api.post("/api/v1/reconcile")
    assert response.status_code == 200
    assert response.json() == {"queued": 0}


# ---- login throttle ----------------------------------------------------------------------

KEY = "k" * 43


async def test_login_throttle_endpoints(api: httpx.AsyncClient) -> None:
    check = await api.post("/api/v1/login-throttle/check", json={"key": KEY})
    assert check.json() == {"retry_after": 0}
    for _ in range(3):
        failure = await api.post("/api/v1/login-throttle/failure", json={"key": KEY})
        assert failure.json() == {"retry_after": 0}
    blocked = await api.post("/api/v1/login-throttle/failure", json={"key": KEY})
    assert blocked.json()["retry_after"] >= 1
    assert (await api.post("/api/v1/login-throttle/check", json={"key": KEY})).json()[
        "retry_after"
    ] >= 1
    ok = await api.post("/api/v1/login-throttle/success", json={"key": KEY})
    assert ok.status_code == 204
    assert (await api.post("/api/v1/login-throttle/check", json={"key": KEY})).json() == {
        "retry_after": 0
    }


@pytest.mark.parametrize("key", ["short", "global", "x" * 129, "has space in it 123456"])
async def test_login_throttle_rejects_bad_keys(api: httpx.AsyncClient, key: str) -> None:
    response = await api.post("/api/v1/login-throttle/check", json={"key": key})
    assert response.status_code == 422


# ---- limits and keyword tester -----------------------------------------------------------


async def test_limits_match_what_the_validator_enforces(api: httpx.AsyncClient) -> None:
    limits = (await api.get("/api/v1/limits")).json()
    assert limits["button_title_max"] == 20
    assert limits["message_max"] == 1000
    # One past each limit is refused, so the numbers shown in the form are the real ones.
    too_long_name = {**CAMPAIGN, "name": "x" * (limits["name_max"] + 1)}
    assert (await api.post("/api/v1/campaigns", json=too_long_name)).status_code == 422
    too_long_button = {**CAMPAIGN, "link_button_label": "x" * (limits["button_title_max"] + 1)}
    assert (await api.post("/api/v1/campaigns", json=too_long_button)).status_code == 422
    at_limit = {**CAMPAIGN, "link_button_label": "x" * limits["button_title_max"]}
    assert (await api.post("/api/v1/campaigns", json=at_limit)).status_code == 201


@pytest.mark.parametrize(
    ("text", "mode", "matched"),
    [
        ("Quero o PREÇO!", "WHOLE_WORD", True),
        ("quero o preco", "WHOLE_WORD", True),
        ("precos", "WHOLE_WORD", False),
        ("precos", "CONTAINS", True),
        ("🔥🔥", "WHOLE_WORD", False),
        ("", "WHOLE_WORD", False),
    ],
)
async def test_match_test_uses_the_real_matcher(
    api: httpx.AsyncClient, text: str, mode: str, matched: bool
) -> None:
    response = await api.post(
        "/api/v1/campaigns/match-test",
        json={"keywords": ["preço"], "match_mode": mode, "text": text},
    )
    assert response.status_code == 200
    assert response.json() == {"matched": matched, "keyword": "preço" if matched else None}


async def test_match_test_does_not_collide_with_campaign_routes(api: httpx.AsyncClient) -> None:
    campaign = (await api.post("/api/v1/campaigns", json=CAMPAIGN)).json()
    assert (await api.get(f"/api/v1/campaigns/{campaign['id']}")).status_code == 200
    bad = await api.post("/api/v1/campaigns/match-test", json={"keywords": "x", "text": "y"})
    assert bad.status_code == 422


async def test_only_a_blocked_login_leaves_an_ops_event(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    for _ in range(3):  # the free failures: typos, not worth an event
        await api.post("/api/v1/login-throttle/failure", json={"key": KEY})
    async with sf() as session:
        assert (await session.execute(select(OpsEvent))).scalars().all() == []
    await api.post("/api/v1/login-throttle/failure", json={"key": KEY})  # now blocked
    async with sf() as session:
        events = (await session.execute(select(OpsEvent))).scalars().all()
    assert len(events) == 1
    assert "blocked after repeated failures" in events[0].message
    assert KEY not in events[0].message
