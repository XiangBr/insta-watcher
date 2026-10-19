"""Public routes: Meta webhook and health. No user authentication here."""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.meta.signature import tokens_equal, verify_signature
from app.models import WebhookEvent

logger = logging.getLogger(__name__)
router = APIRouter()


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


class BodyTooLarge(Exception):
    pass


async def _read_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/webhook", include_in_schema=False)
async def webhook_challenge(
    settings: SettingsDep,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge", max_length=256)] = None,
) -> Response:
    expected = settings.webhook_verify_token.get_secret_value()
    if mode == "subscribe" and challenge is not None and tokens_equal(token, expected):
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403)


@router.post("/webhook", include_in_schema=False)
async def webhook_receive(
    request: Request, settings: SettingsDep, session_factory: SessionFactoryDep
) -> Response:
    try:
        body = await _read_limited(request, settings.webhook_max_body_bytes)
    except BodyTooLarge:
        logger.warning("webhook rejected: body over %d bytes", settings.webhook_max_body_bytes)
        raise HTTPException(status_code=413) from None

    # Authenticity first, on the raw bytes, before any parsing.
    header = request.headers.get("x-hub-signature-256")
    if not verify_signature(body, header, settings.app_secrets()):
        # Never log the body: it carries third-party comment text.
        logger.warning(
            "webhook rejected: invalid signature (size=%d, header_present=%s)",
            len(body),
            header is not None,
        )
        raise HTTPException(status_code=401)

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400) from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400)

    # Store and acknowledge. Processing happens in the dispatcher, never in the request.
    async with session_factory() as session, session.begin():
        session.add(WebhookEvent(payload=payload))
    wake = getattr(request.app.state, "dispatch_wake", None)
    if wake is not None:
        wake.set()
    return JSONResponse({"status": "ok"})


@router.get("/health", include_in_schema=False)
async def health(session_factory: SessionFactoryDep) -> Response:
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health check: database unavailable")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})
