import hashlib
import hmac
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.config import Config
from fastapi import FastAPI

from alembic import command
from app.config import Settings
from app.factory import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Loops are exercised by calling their steps directly, never in the background.
os.environ["BACKGROUND_TASKS_ENABLED"] = "false"


def strong_secrets() -> dict[str, Any]:
    return {
        "meta_app_secret": secrets.token_hex(16),
        "instagram_app_secret": secrets.token_hex(16),
        "webhook_verify_token": secrets.token_hex(24),
        "encryption_key": secrets.token_hex(32),
        "api_admin_token": secrets.token_hex(32),
        "click_hash_salt": secrets.token_hex(32),
    }


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(_env_file=None, database_url=database_url, **strong_secrets())  # type: ignore[call-arg]


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
