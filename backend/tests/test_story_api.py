"""Campaign triggers and story-reply deliveries through the admin API and the CLI."""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import main
from app.config import Settings, get_settings
from app.models import (
    Delivery,
    DeliveryStatus,
    Job,
    JobStatus,
    JobType,
    Origin,
    Trigger,
    WebhookEvent,
)
from tests.conftest import strong_secrets
from tests.factories import POST_ID, add_campaign, story_reply_payload

BASE: dict[str, Any] = {
    "name": "stories",
    "keywords": ["link"],
    "dm_message": "Oi {username}!",
}


@pytest.fixture
async def api(client: httpx.AsyncClient, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    client.headers["authorization"] = f"Bearer {settings.api_admin_token.get_secret_value()}"
    yield client


@pytest.fixture
def sf(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    return factory


async def story_delivery(
    sf: async_sessionmaker[AsyncSession],
    campaign_id: int,
    status: DeliveryStatus,
    job_status: JobStatus,
    *,
    with_job: bool = True,
) -> int:
    async with sf() as session, session.begin():
        delivery = Delivery(
            campaign_id=campaign_id,
            trigger=Trigger.STORY_REPLY,
            origin=Origin.WEBHOOK,
            source_id="mid_1",
            actor_id="1",
            text="link",
            matched_keyword="link",
            status=status,
        )
        session.add(delivery)
        await session.flush()
        if with_job:
            session.add(
                Job(
                    type=JobType.DIRECT_MESSAGE,
                    delivery_id=delivery.id,
                    dedupe_key=f"direct:{delivery.id}",
                    status=job_status,
                    attempts=2,
                )
            )
        return delivery.id


# ---- campaign triggers ---------------------------------------------------------------------


async def test_a_story_only_campaign_needs_no_post(api: httpx.AsyncClient) -> None:
    body = {**BASE, "trigger_comments": False, "trigger_story_replies": True}
    response = await api.post("/api/v1/campaigns", json=body)
    assert response.status_code == 201
    campaign = response.json()
    assert campaign["trigger_comments"] is False
    assert campaign["trigger_story_replies"] is True
    assert campaign["post_id"] is None
    assert campaign["match_any_post"] is False


async def test_a_campaign_can_answer_comments_and_stories(api: httpx.AsyncClient) -> None:
    body = {**BASE, "post_id": POST_ID, "trigger_story_replies": True}
    campaign = (await api.post("/api/v1/campaigns", json=body)).json()
    assert campaign["trigger_comments"] is True
    assert campaign["trigger_story_replies"] is True


async def test_existing_style_campaigns_still_work(api: httpx.AsyncClient) -> None:
    campaign = (await api.post("/api/v1/campaigns", json={**BASE, "post_id": POST_ID})).json()
    assert campaign["trigger_comments"] is True
    assert campaign["trigger_story_replies"] is False


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ({"trigger_comments": False}, "at least one trigger"),
        (
            {"trigger_comments": False, "trigger_story_replies": True, "post_id": POST_ID},
            "post target",
        ),
        (
            {"trigger_comments": False, "trigger_story_replies": True, "match_any_post": True},
            "post target",
        ),
        (
            {
                "trigger_comments": False,
                "trigger_story_replies": True,
                "public_reply_messages": ["Te mandei!"],
            },
            "public replies",
        ),
        ({"trigger_story_replies": True}, "exactly one"),  # comments on, but no post chosen
    ],
)
async def test_invalid_trigger_combinations_are_refused(
    api: httpx.AsyncClient, extra: dict[str, Any], needle: str
) -> None:
    response = await api.post("/api/v1/campaigns", json={**BASE, **extra})
    assert response.status_code == 422
    assert needle in response.text


async def test_a_campaign_can_be_switched_to_story_replies(api: httpx.AsyncClient) -> None:
    campaign = (await api.post("/api/v1/campaigns", json={**BASE, "post_id": POST_ID})).json()
    patched = await api.patch(
        f"/api/v1/campaigns/{campaign['id']}",
        json={"trigger_story_replies": True, "trigger_comments": False, "post_id": None},
    )
    assert patched.status_code == 200
    assert patched.json()["trigger_story_replies"] is True
    assert patched.json()["post_id"] is None
    copy = (await api.post(f"/api/v1/campaigns/{campaign['id']}/duplicate")).json()
    assert copy["trigger_story_replies"] is True  # the copy keeps the triggers


# ---- deliveries ----------------------------------------------------------------------------


async def test_retrying_a_story_delivery_requeues_the_direct_message_job(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    campaign = await add_campaign(sf)
    delivery_id = await story_delivery(sf, campaign.id, DeliveryStatus.FAILED, JobStatus.FAILED)

    response = await api.post(f"/api/v1/deliveries/{delivery_id}/retry")
    assert response.status_code == 200
    assert response.json()["trigger"] == "STORY_REPLY"
    async with sf() as session:
        jobs = (await session.execute(select(Job))).scalars().all()
    assert [(j.type, j.status, j.attempts) for j in jobs] == [
        (JobType.DIRECT_MESSAGE, JobStatus.PENDING, 0)
    ]


async def test_a_missing_story_job_is_recreated_as_a_direct_message(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    campaign = await add_campaign(sf)
    delivery_id = await story_delivery(
        sf, campaign.id, DeliveryStatus.FAILED, JobStatus.FAILED, with_job=False
    )
    assert (await api.post(f"/api/v1/deliveries/{delivery_id}/retry")).status_code == 200
    async with sf() as session:
        [job] = (await session.execute(select(Job))).scalars().all()
    assert job.type == JobType.DIRECT_MESSAGE
    assert job.dedupe_key == f"direct:{delivery_id}"


async def test_an_unconfirmed_story_dm_still_needs_the_inbox_check(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    campaign = await add_campaign(sf)
    delivery_id = await story_delivery(
        sf, campaign.id, DeliveryStatus.UNCONFIRMED, JobStatus.UNCONFIRMED
    )
    assert (await api.post(f"/api/v1/deliveries/{delivery_id}/retry")).status_code == 422


async def test_webhook_events_tell_a_story_reply_from_a_plain_message(
    api: httpx.AsyncClient, sf: async_sessionmaker[AsyncSession]
) -> None:
    async with sf() as session, session.begin():
        session.add(WebhookEvent(payload=story_reply_payload(mid="a", text="segredo")))
        session.add(WebhookEvent(payload=story_reply_payload(mid="b", story_id=None)))
        session.add(
            WebhookEvent(payload=story_reply_payload(mid="c", is_echo=True, shape="changes"))
        )
    events = (await api.get("/api/v1/webhook-events")).json()
    kinds = [e["changes"][0]["kind"] for e in reversed(events)]
    assert kinds == ["story_reply", "message", "echo"]
    assert "segredo" not in str(events)  # the text stays hidden unless asked for
    with_text = (await api.get("/api/v1/webhook-events", params={"with_text": True})).json()
    assert "segredo" in str(with_text)


# ---- CLI -----------------------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> Iterator[None]:
    for name, value in strong_secrets().items():
        monkeypatch.setenv(name.upper(), value)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_cli_creates_and_lists_a_story_only_campaign(
    cli_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["campaign", "create", "--name", "stories", "--keyword", "link"]
    args += ["--no-comments", "--story-replies", "--message", "Oi {username}!"]
    assert main(args) == 0
    capsys.readouterr()
    assert main(["campaign", "list"]) == 0
    assert "story replies" in capsys.readouterr().out


def test_the_cli_refuses_a_campaign_with_no_trigger_target(
    cli_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["campaign", "create", "--name", "x", "--keyword", "link", "--message", "Oi"]
    assert main(args) == 1  # comments are on but no post was chosen
    assert "exactly one" in capsys.readouterr().out
