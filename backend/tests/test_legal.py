from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.factory import create_app
from tests.conftest import strong_secrets

PAGES = ["/privacy", "/terms", "/data-deletion"]
CONTACT = "dono@exemplo.org"


async def _client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def legal_client(database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=database_url,
        legal_contact_email=CONTACT,
        **strong_secrets(),
    )
    async for client in _client(settings):
        yield client


@pytest.mark.parametrize("path", PAGES)
async def test_pages_render_with_contact(legal_client: httpx.AsyncClient, path: str) -> None:
    response = await legal_client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert CONTACT in response.text
    assert "<script" not in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]


@pytest.mark.parametrize("path", PAGES)
async def test_pages_are_404_without_contact(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "value", ["not-an-email", "a@b", '<script>"x"@y.com', "x@y.com<img src=x>"]
)
def test_contact_must_be_a_plain_email(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="sqlite+aiosqlite:///:memory:",
            legal_contact_email=value,
            **strong_secrets(),
        )
