"""`cli doctor`: says what is missing, with the next step, and never prints a secret."""

import httpx
import respx
from fastapi import FastAPI

from app.config import Settings
from app.db import utcnow
from app.doctor import Level, failed, render, run_checks
from app.meta.client import GRAPH_HOST, MetaClient
from app.models import Account
from app.tokens import cipher_for
from tests.conftest import strong_secrets

TOKEN = "IGAAdoctortoken" + "d" * 40


def test_empty_optional_variables_mean_unset(database_url: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=database_url,
        public_url="",
        legal_contact_email="",
        **strong_secrets(),
    )
    assert settings.public_url is None and settings.legal_contact_email is None


async def test_fresh_install_lists_every_gap(app: FastAPI, settings: Settings) -> None:
    async with httpx.AsyncClient() as http:
        checks = await run_checks(
            app.state.session_factory, settings, MetaClient(http, "v25.0"), http
        )
    text = render(checks)
    assert failed(checks)
    assert "legal pages off" in text and "LEGAL_CONTACT_EMAIL" in text
    assert "no Instagram token" in text and "./scripts/iw set-token" in text
    assert "PUBLIC_URL not set" in text
    assert settings.webhook_verify_token.get_secret_value() not in text


@respx.mock
async def test_ready_install_passes(database_url: str) -> None:
    from app.factory import create_app

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=database_url,
        legal_contact_email="contato@exemplo.com.br",
        public_url="https://insta.exemplo.com.br",
        **strong_secrets(),
    )
    application = create_app(settings)
    respx.get(f"{GRAPH_HOST}/v25.0/me").mock(
        return_value=httpx.Response(200, json={"user_id": "1", "username": "conta"})
    )
    respx.get("https://insta.exemplo.com.br/health").mock(return_value=httpx.Response(200))
    async with application.router.lifespan_context(application):
        sf = application.state.session_factory
        async with sf() as session, session.begin():
            session.add(
                Account(
                    id=1,
                    ig_user_id="1",
                    username="conta",
                    token_encrypted=cipher_for(settings).encrypt(TOKEN),
                    token_set_at=utcnow(),
                    webhook_subscribed_at=utcnow(),
                )
            )
        async with httpx.AsyncClient() as http:
            checks = await run_checks(sf, settings, MetaClient(http, "v25.0"), http)
    assert not failed(checks), render(checks)
    levels = {c.title: c.level for c in checks}
    assert levels["token valid"] == Level.OK
    assert levels["public URL answers"] == Level.OK
    assert "https://insta.exemplo.com.br/webhook" in render(checks)
    assert TOKEN not in render(checks)
