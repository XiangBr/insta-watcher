import secrets
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import select, text

from app.config import Settings
from app.db import utcnow
from app.meta.client import GRAPH_HOST, MetaClient
from app.models import Account, AccountStatus, OpsEvent
from app.tokens import (
    DifferentAccountError,
    TokenCipher,
    TokenDecryptError,
    load_token,
    refresh_due,
    refresh_token_if_due,
    set_account_token,
)

TOKEN = "IGAAplaintexttoken" + "x" * 40
ME = f"{GRAPH_HOST}/v25.0/me"
REFRESH = f"{GRAPH_HOST}/refresh_access_token"


@pytest.fixture
async def meta() -> AsyncIterator[MetaClient]:
    async with httpx.AsyncClient() as http:
        yield MetaClient(http, "v25.0")


def me_ok(user_id: str = "17841400000000001", username: str = "dono") -> httpx.Response:
    return httpx.Response(200, json={"user_id": user_id, "username": username, "id": "999"})


# ---- cipher ----


def test_cipher_round_trip_and_random_nonce() -> None:
    cipher = TokenCipher(secrets.token_hex(32))
    a, b = cipher.encrypt(TOKEN), cipher.encrypt(TOKEN)
    assert a != b
    assert cipher.decrypt(a) == TOKEN
    assert TOKEN.encode() not in a


def test_cipher_rejects_tampering_and_wrong_key() -> None:
    key = secrets.token_hex(32)
    blob = bytearray(TokenCipher(key).encrypt(TOKEN))
    blob[-1] ^= 1
    with pytest.raises(TokenDecryptError):
        TokenCipher(key).decrypt(bytes(blob))
    with pytest.raises(TokenDecryptError):
        TokenCipher(secrets.token_hex(32)).decrypt(TokenCipher(key).encrypt(TOKEN))


# ---- set / load ----


@respx.mock
async def test_set_token_stores_only_ciphertext(
    app: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(ME).mock(return_value=me_ok())
    info = await set_account_token(app.state.session_factory, settings, meta, TOKEN)
    assert info.username == "dono"
    async with app.state.session_factory() as session:
        raw = (await session.execute(text("SELECT token_encrypted FROM account"))).scalar_one()
        assert TOKEN.encode() not in raw
        loaded = await load_token(session, settings)
        ops = (await session.execute(select(OpsEvent.message))).scalars().all()
    assert loaded is not None
    assert loaded[1] == TOKEN
    assert loaded[0].status == AccountStatus.ACTIVE
    assert loaded[0].token_expiry_confirmed is False
    assert all(TOKEN not in m for m in ops)


@respx.mock
async def test_token_for_another_account_is_refused(
    app: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    respx.get(ME).mock(side_effect=[me_ok(), me_ok(user_id="17841400000000002", username="x")])
    await set_account_token(app.state.session_factory, settings, meta, TOKEN)
    with pytest.raises(DifferentAccountError):
        await set_account_token(app.state.session_factory, settings, meta, TOKEN + "2")


# ---- refresh ----


async def stored_account(app: FastAPI, settings: Settings, meta: MetaClient) -> None:
    with respx.mock:
        respx.get(ME).mock(return_value=me_ok())
        await set_account_token(
            app.state.session_factory, settings, meta, TOKEN, now=utcnow() - timedelta(days=2)
        )


async def get_account(app: FastAPI) -> Account:
    async with app.state.session_factory() as session:
        account = await session.get(Account, 1)
    assert account is not None
    return account


def test_refresh_waits_until_token_is_24h_old(settings: Settings) -> None:
    now = utcnow()
    account = Account(
        status=AccountStatus.ACTIVE, token_set_at=now - timedelta(hours=23), token_expires_at=None
    )
    account.token_expiry_confirmed = False
    assert not refresh_due(account, settings, now)
    account.token_set_at = now - timedelta(hours=25)
    assert refresh_due(account, settings, now)


def test_confirmed_token_refreshes_only_near_expiry(settings: Settings) -> None:
    now = utcnow()
    account = Account(
        status=AccountStatus.ACTIVE,
        token_set_at=now - timedelta(days=5),
        token_refreshed_at=now - timedelta(days=5),
        token_expires_at=now + timedelta(days=55),
        token_expiry_confirmed=True,
    )
    assert not refresh_due(account, settings, now)
    account.token_expires_at = now + timedelta(days=settings.token_refresh_when_days)
    assert refresh_due(account, settings, now)


async def test_refresh_success_confirms_lifetime(
    app: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await stored_account(app, settings, meta)
    with respx.mock:
        route = respx.get(REFRESH).mock(
            return_value=httpx.Response(
                200, json={"access_token": "IGAAnew" + "y" * 40, "expires_in": 5184000}
            )
        )
        assert await refresh_token_if_due(app.state.session_factory, settings, meta)
        assert "authorization" not in route.calls.last.request.headers
    account = await get_account(app)
    assert account.token_expiry_confirmed
    assert account.token_expires_at is not None
    assert (account.token_expires_at - utcnow()).days in (59, 60)
    async with app.state.session_factory() as session:
        loaded = await load_token(session, settings)
    assert loaded is not None
    assert loaded[1].startswith("IGAAnew")


async def test_refresh_rejected_token_marks_account_invalid(
    app: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await stored_account(app, settings, meta)
    with respx.mock:
        respx.get(REFRESH).mock(
            return_value=httpx.Response(
                400, json={"error": {"message": "expired", "code": 190, "type": "OAuthException"}}
            )
        )
        assert not await refresh_token_if_due(app.state.session_factory, settings, meta)
    account = await get_account(app)
    assert account.status == AccountStatus.TOKEN_INVALID
    async with app.state.session_factory() as session:
        levels = (await session.execute(select(OpsEvent.level))).scalars().all()
    assert "ERROR" in levels


async def test_refresh_ambiguous_failure_keeps_account_active_and_waits_a_day(
    app: FastAPI, settings: Settings, meta: MetaClient
) -> None:
    await stored_account(app, settings, meta)
    with respx.mock:
        route = respx.get(REFRESH).mock(side_effect=httpx.ReadTimeout("slow"))
        assert not await refresh_token_if_due(app.state.session_factory, settings, meta)
        assert not await refresh_token_if_due(app.state.session_factory, settings, meta)
        assert route.call_count == 1
    assert (await get_account(app)).status == AccountStatus.ACTIVE
