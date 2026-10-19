"""Instagram token at rest (AES-256-GCM) and its lifecycle: set, load, refresh.

The plaintext token only exists in memory, for the duration of a call to Meta.
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import utcnow
from app.meta.client import MetaClient
from app.meta.errors import ConfirmedRejection, MetaError, RejectionKind
from app.meta.limits import TOKEN_LIFETIME_ASSUMED, TOKEN_MIN_AGE_FOR_REFRESH
from app.models import Account, AccountStatus
from app.ops import OpsLevel, OpsSource, record

_VERSION = b"v1"
_NONCE_BYTES = 12
# Binds the ciphertext to its purpose; a blob from another context will not decrypt here.
_AAD = b"insta-watcher/account-token/v1"
_PASTE_MARKERS = re.compile(r"\x1b\[20[01]~")


def clean_pasted_token(raw: str) -> str:
    """Drop what terminals add around a paste: bracketed-paste markers, whitespace, controls."""
    without_markers = _PASTE_MARKERS.sub("", raw)
    return "".join(ch for ch in without_markers if ch.isprintable() and not ch.isspace())


class TokenDecryptError(Exception):
    """Wrong ENCRYPTION_KEY or corrupted data. The owner must paste the token again."""


class TokenCipher:
    def __init__(self, key_hex: str) -> None:
        self._aead = AESGCM(bytes.fromhex(key_hex))

    def encrypt(self, token: str) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return _VERSION + nonce + self._aead.encrypt(nonce, token.encode(), _AAD)

    def decrypt(self, blob: bytes) -> str:
        if not blob.startswith(_VERSION):
            raise TokenDecryptError("unknown token format")
        nonce = blob[len(_VERSION) : len(_VERSION) + _NONCE_BYTES]
        ciphertext = blob[len(_VERSION) + _NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, ciphertext, _AAD).decode()
        except InvalidTag:
            raise TokenDecryptError("token does not decrypt with ENCRYPTION_KEY") from None


def cipher_for(settings: Settings) -> TokenCipher:
    return TokenCipher(settings.encryption_key.get_secret_value())


class DifferentAccountError(Exception):
    """The new token belongs to another Instagram account. v1 serves exactly one."""


@dataclass(frozen=True)
class AccountInfo:
    ig_user_id: str
    username: str


async def set_account_token(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    meta: MetaClient,
    token: str,
    now: datetime | None = None,
) -> AccountInfo:
    """Validate the token against /me and store it encrypted. Raises MetaError if invalid."""
    now = now or utcnow()
    me = await meta.me(token)
    ig_user_id = str(me.get("user_id") or "")
    username = str(me.get("username") or "")
    if not ig_user_id:
        raise DifferentAccountError("token did not return a professional account user_id")
    blob = cipher_for(settings).encrypt(token)
    async with session_factory() as session, session.begin():
        account = await session.get(Account, 1)
        if account is not None and account.ig_user_id != ig_user_id:
            raise DifferentAccountError(
                f"this instance serves @{account.username}; the token is for @{username}"
            )
        if account is None:
            account = Account(id=1, ig_user_id=ig_user_id)
            session.add(account)
        account.username = username
        account.token_encrypted = blob
        account.token_set_at = now
        # Real lifetime is learned on the first refresh (allowed once the token is 24 h old).
        account.token_expires_at = now + TOKEN_LIFETIME_ASSUMED
        account.token_expiry_confirmed = False
        account.token_refreshed_at = None
        account.token_refresh_attempted_at = None
        account.status = AccountStatus.ACTIVE
        record(session, OpsSource.TOKEN, OpsLevel.INFO, f"token set for @{username}")
    return AccountInfo(ig_user_id=ig_user_id, username=username)


async def load_token(session: AsyncSession, settings: Settings) -> tuple[Account, str] | None:
    account = await session.get(Account, 1)
    if account is None:
        return None
    return account, cipher_for(settings).decrypt(account.token_encrypted)


def refresh_due(account: Account, settings: Settings, now: datetime) -> bool:
    if account.status != AccountStatus.ACTIVE:
        return False
    if now - (account.token_refreshed_at or account.token_set_at) < TOKEN_MIN_AGE_FOR_REFRESH:
        return False
    last_try = account.token_refresh_attempted_at
    if last_try is not None and now - last_try < timedelta(hours=24):
        return False
    if not account.token_expiry_confirmed or account.token_expires_at is None:
        return True  # learn the real lifetime as soon as Meta allows a refresh
    return account.token_expires_at - now <= timedelta(days=settings.token_refresh_when_days)


async def refresh_token_if_due(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    meta: MetaClient,
    now: datetime | None = None,
) -> bool:
    """Refresh the long-lived token when due. Returns True if a new token was stored."""
    now = now or utcnow()
    async with session_factory() as session, session.begin():
        loaded = await load_token(session, settings)
        if loaded is None:
            return False
        account, token = loaded
        if not refresh_due(account, settings, now):
            return False
        account.token_refresh_attempted_at = now
    try:
        refreshed = await meta.refresh_token(token)
    except MetaError as exc:
        async with session_factory() as session, session.begin():
            current = await session.get(Account, 1)
            if current is None:  # removed meanwhile
                return False
            invalid = isinstance(exc, ConfirmedRejection) and (
                exc.kind == RejectionKind.TOKEN_INVALID
            )
            if invalid:
                current.status = AccountStatus.TOKEN_INVALID
            days_left = (current.token_expires_at - now).days if current.token_expires_at else None
            record(
                session,
                OpsSource.TOKEN,
                OpsLevel.ERROR,
                f"token refresh failed ({days_left} days left, retry in 24 h): {exc}",
            )
        return False
    async with session_factory() as session, session.begin():
        current = await session.get(Account, 1)
        if current is None:
            return False
        current.token_encrypted = cipher_for(settings).encrypt(refreshed.access_token)
        current.token_refreshed_at = now
        current.token_expires_at = now + timedelta(seconds=refreshed.expires_in_seconds)
        current.token_expiry_confirmed = True
        record(
            session,
            OpsSource.TOKEN,
            OpsLevel.INFO,
            f"token refreshed, valid for {refreshed.expires_in_seconds // 86400} days",
        )
    return True


async def mark_token_invalid(session: AsyncSession, reason: str) -> None:
    account = await session.get(Account, 1)
    if account is not None and account.status != AccountStatus.TOKEN_INVALID:
        account.status = AccountStatus.TOKEN_INVALID
        record(session, OpsSource.TOKEN, OpsLevel.ERROR, f"token rejected by Meta: {reason}")
