from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy import select

from app.config import Settings
from app.db import utcnow
from app.maintenance import apply_retention
from app.models import Delivery, OpsEvent, WebhookEvent
from tests.factories import add_campaign


async def test_retention_deletes_only_expired_rows(app: FastAPI, settings: Settings) -> None:
    now = utcnow()
    factory = app.state.session_factory
    async with factory() as session, session.begin():
        session.add_all(
            [
                WebhookEvent(payload={"n": "old"}, received_at=now - timedelta(days=8)),
                WebhookEvent(payload={"n": "edge"}, received_at=now - timedelta(days=6)),
                WebhookEvent(payload={"n": "new"}, received_at=now),
                OpsEvent(
                    source="SYSTEM",
                    level="INFO",
                    message="old",
                    created_at=now - timedelta(days=91),
                ),
                OpsEvent(source="SYSTEM", level="INFO", message="new", created_at=now),
            ]
        )

    result = await apply_retention(factory, settings, now=now)

    assert result.webhook_events_deleted == 1
    assert result.ops_events_deleted == 1
    async with factory() as session:
        payloads = (await session.execute(select(WebhookEvent.payload))).scalars().all()
        messages = (await session.execute(select(OpsEvent.message))).scalars().all()
    assert sorted(p["n"] for p in payloads) == ["edge", "new"]
    assert messages == ["new"]


async def test_retention_respects_configured_days(app: FastAPI, settings: Settings) -> None:
    now = utcnow()
    factory = app.state.session_factory
    async with factory() as session, session.begin():
        session.add(WebhookEvent(payload={}, received_at=now - timedelta(days=2)))
    short = settings.model_copy(update={"retention_webhook_days": 1})
    assert (await apply_retention(factory, short, now=now)).webhook_events_deleted == 1


async def test_old_deliveries_are_anonymized_but_kept(app: FastAPI, settings: Settings) -> None:
    factory = app.state.session_factory
    campaign = await add_campaign(factory)
    now = utcnow()
    async with factory() as session, session.begin():
        for days, source in ((91, "c_old"), (10, "c_new")):
            session.add(
                Delivery(
                    campaign_id=campaign.id,
                    source_id=source,
                    actor_id="28542923325365400",
                    actor_username="fan",
                    text="quero o link",
                    created_at=now - timedelta(days=days),
                )
            )

    result = await apply_retention(factory, settings, now=now)

    assert result.deliveries_anonymized == 1
    async with factory() as session:
        rows = (await session.execute(select(Delivery).order_by(Delivery.id))).scalars().all()
    old, new = rows
    assert (old.text, old.actor_username, old.actor_id) == (None, None, "anon")
    assert old.source_id == f"anon:{old.id}"
    assert old.anonymized_at is not None
    assert (new.text, new.actor_username) == ("quero o link", "fan")
    assert (await apply_retention(factory, settings, now=now)).deliveries_anonymized == 0
