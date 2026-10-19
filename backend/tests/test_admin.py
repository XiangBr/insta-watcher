import httpx
import pytest

from app.config import Settings


@pytest.mark.parametrize(
    "header",
    [None, "Bearer wrong", "Basic abc", "Bearer", ""],
)
async def test_status_requires_admin_token(client: httpx.AsyncClient, header: str | None) -> None:
    headers = {"authorization": header} if header is not None else {}
    response = await client.get("/api/v1/status", headers=headers)
    assert response.status_code == 401


async def test_status_with_admin_token(client: httpx.AsyncClient, settings: Settings) -> None:
    token = settings.api_admin_token.get_secret_value()
    response = await client.get("/api/v1/status", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["account"] is None
    assert body["rate_limit"] == {"used": 0, "limit": 750}
    assert token not in response.text
