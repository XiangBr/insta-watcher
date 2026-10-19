import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.factory import create_app
from app.models import WebhookEvent
from tests.conftest import sign, strong_secrets

PAYLOAD = {"object": "instagram", "entry": [{"id": "1784", "changes": []}]}
BODY = json.dumps(PAYLOAD).encode()


async def count_events(app: FastAPI) -> int:
    async with app.state.session_factory() as session:
        return int((await session.execute(select(func.count(WebhookEvent.id)))).scalar_one())


async def post(client: httpx.AsyncClient, body: bytes, signature: str | None) -> httpx.Response:
    headers = {"content-type": "application/json"}
    if signature is not None:
        headers["x-hub-signature-256"] = signature
    return await client.post("/webhook", content=body, headers=headers)


# ---- GET /webhook (subscription challenge) ----


async def test_challenge_with_valid_token(client: httpx.AsyncClient, settings: Settings) -> None:
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": settings.webhook_verify_token.get_secret_value(),
        "hub.challenge": "1158201444",
    }
    response = await client.get("/webhook", params=params)
    assert response.status_code == 200
    assert response.text == "1158201444"


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": "__VALID__", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": "__VALID__"},
    ],
)
async def test_challenge_rejected(
    client: httpx.AsyncClient, settings: Settings, params: dict[str, str]
) -> None:
    if params.get("hub.verify_token") == "__VALID__":
        params = {**params, "hub.verify_token": settings.webhook_verify_token.get_secret_value()}
    response = await client.get("/webhook", params=params)
    assert response.status_code == 403


# ---- POST /webhook ----


async def test_valid_signature_stores_event(
    client: httpx.AsyncClient, app: FastAPI, settings: Settings
) -> None:
    secret = settings.meta_app_secret.get_secret_value()
    response = await post(client, BODY, sign(BODY, secret))
    assert response.status_code == 200
    async with app.state.session_factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert event.payload == PAYLOAD
    assert event.status == "PENDING"
    assert event.received_at.tzinfo is not None


async def test_instagram_secret_also_accepted(
    client: httpx.AsyncClient, app: FastAPI, settings: Settings
) -> None:
    secret = settings.instagram_app_secret.get_secret_value()
    response = await post(client, BODY, sign(BODY, secret))
    assert response.status_code == 200
    assert await count_events(app) == 1


async def test_invalid_signature_is_rejected_and_not_stored(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    response = await post(client, BODY, sign(BODY, "f0" * 16))
    assert response.status_code == 401
    assert await count_events(app) == 0


async def test_missing_signature_is_rejected(client: httpx.AsyncClient, app: FastAPI) -> None:
    response = await post(client, BODY, None)
    assert response.status_code == 401
    assert await count_events(app) == 0


async def test_altered_body_is_rejected(
    client: httpx.AsyncClient, app: FastAPI, settings: Settings
) -> None:
    signature = sign(BODY, settings.meta_app_secret.get_secret_value())
    tampered = BODY.replace(b"1784", b"9999")
    response = await post(client, tampered, signature)
    assert response.status_code == 401
    assert await count_events(app) == 0


async def test_oversized_body_is_rejected(
    client: httpx.AsyncClient, app: FastAPI, settings: Settings
) -> None:
    body = b'{"x":"' + b"a" * settings.webhook_max_body_bytes + b'"}'
    response = await post(client, body, sign(body, settings.meta_app_secret.get_secret_value()))
    assert response.status_code == 413
    assert await count_events(app) == 0


@pytest.mark.parametrize("body", [b"not json", b"[1,2,3]", b"\xff\xfe"])
async def test_signed_but_malformed_body_is_400(
    client: httpx.AsyncClient, app: FastAPI, settings: Settings, body: bytes
) -> None:
    response = await post(client, body, sign(body, settings.meta_app_secret.get_secret_value()))
    assert response.status_code == 400
    assert await count_events(app) == 0


async def test_rejected_body_is_not_logged(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    body = b'{"text":"comentario-de-terceiro-unico"}'
    await post(client, body, "sha256=" + "0" * 64)
    assert "comentario-de-terceiro-unico" not in caplog.text


async def test_database_errors_do_not_carry_payload(tmp_path: Path) -> None:
    # No migration on this database, so the insert fails.
    url = f"sqlite+aiosqlite:///{(tmp_path / 'empty.db').as_posix()}"
    settings = Settings(_env_file=None, database_url=url, **strong_secrets())  # type: ignore[arg-type]
    application = create_app(settings)
    body = b'{"text":"comentario-de-terceiro-unico"}'
    signature = sign(body, settings.meta_app_secret.get_secret_value())
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            with pytest.raises(OperationalError) as excinfo:
                await post(c, body, signature)
    assert "comentario-de-terceiro-unico" not in str(excinfo.value)


# ---- health and docs ----


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_api_docs_are_off(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 404
