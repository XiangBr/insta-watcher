"""Apify client: start an actor run, check on it, read its dataset. Only what the competitor
analysis needs. The token goes in the Authorization header, never in a URL, so it cannot leak
through an httpx log line or an error message."""

from dataclasses import dataclass
from typing import Any

import httpx

APIFY_HOST = "https://api.apify.com"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Apify run states. Anything else (READY, RUNNING, ABORTING, TIMING-OUT) is still going.
FINISHED_OK = frozenset({"SUCCEEDED"})
FINISHED_BAD = frozenset({"FAILED", "ABORTED", "TIMED-OUT"})


class ApifyError(Exception):
    """A failed call. The message is safe to store and show: status and Apify's own text."""


@dataclass(frozen=True)
class ApifyRun:
    id: str
    status: str
    dataset_id: str | None
    cost_usd: float | None

    @property
    def finished(self) -> bool:
        return self.status in FINISHED_OK or self.status in FINISHED_BAD

    @property
    def succeeded(self) -> bool:
        return self.status in FINISHED_OK


def _run(body: Any) -> ApifyRun:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or not data.get("id"):
        raise ApifyError("Apify answered without a run")
    cost = data.get("usageTotalUsd")
    return ApifyRun(
        id=str(data["id"]),
        status=str(data.get("status") or ""),
        dataset_id=str(data["defaultDatasetId"]) if data.get("defaultDatasetId") else None,
        cost_usd=float(cost) if isinstance(cost, int | float) else None,
    )


class ApifyClient:
    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(
                method, f"{APIFY_HOST}{path}", headers=self._headers, timeout=TIMEOUT, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ApifyError(f"Apify unreachable ({type(exc).__name__})") from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400:
            message = ""
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                message = str(body["error"].get("message") or "")[:200]
            raise ApifyError(f"Apify HTTP {response.status_code}: {message}".rstrip(": "))
        return body

    async def start_profiles(self, actor: str, usernames: list[str]) -> ApifyRun:
        """Start the profile scraper. maxItems caps what a pay-per-result actor can bill."""
        body = await self._request(
            "POST",
            f"/v2/acts/{actor}/runs",
            params={"maxItems": str(len(usernames))},
            json={"usernames": usernames},
        )
        return _run(body)

    async def get_run(self, run_id: str) -> ApifyRun:
        return _run(await self._request("GET", f"/v2/actor-runs/{run_id}"))

    async def dataset_items(self, dataset_id: str) -> list[dict[str, Any]]:
        body = await self._request(
            "GET", f"/v2/datasets/{dataset_id}/items", params={"clean": "true", "format": "json"}
        )
        if not isinstance(body, list):
            raise ApifyError("Apify dataset is not a list")
        return [item for item in body if isinstance(item, dict)]
