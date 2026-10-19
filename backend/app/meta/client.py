"""Async client for graph.instagram.com. Fixed host, token in the Authorization header.

Every failure surfaces as NotSent, AmbiguousFailure or ConfirmedRejection (app.meta.errors).
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.meta.errors import AmbiguousFailure, NotSent, classify_error_response

GRAPH_HOST = "https://graph.instagram.com"
# Webhook fields the account is subscribed to. messaging_postbacks carries the tap on a
# postback button (the follow gate), and is delivered only if both the app and the account
# are subscribed to it.
SUBSCRIBED_FIELDS = "comments,messages,messaging_postbacks"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


@dataclass(frozen=True)
class LinkButton:
    title: str
    url: str


@dataclass(frozen=True)
class PostbackButton:
    """A button that sends us an event when tapped, instead of opening a link."""

    title: str
    payload: str


Button = LinkButton | PostbackButton


@dataclass(frozen=True)
class QuickReply:
    """A tappable option under a text message. The tap comes back as a message with this
    payload in message.quick_reply.payload."""

    title: str
    payload: str


@dataclass(frozen=True)
class RefreshedToken:
    access_token: str
    expires_in_seconds: int


class MetaClient:
    def __init__(self, http: httpx.AsyncClient, api_version: str) -> None:
        self._http = http
        self._version = api_version

    async def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        versioned: bool = True,
        auth_header: bool = True,
    ) -> dict[str, Any]:
        url = f"{GRAPH_HOST}/{self._version}/{path}" if versioned else f"{GRAPH_HOST}/{path}"
        headers = {"Authorization": f"Bearer {token}"} if auth_header else {}
        try:
            response = await self._http.request(
                method, url, json=json, params=params, headers=headers, timeout=DEFAULT_TIMEOUT
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise NotSent(f"connection failed: {type(exc).__name__}") from None
        except httpx.HTTPError as exc:
            # The request may have been written to the socket. Do not guess.
            raise AmbiguousFailure(f"transport error after send: {type(exc).__name__}") from None
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        if not response.is_success:
            raise classify_error_response(response.status_code, body)
        if not isinstance(body, dict):
            raise AmbiguousFailure(f"http {response.status_code} with a non-JSON body")
        return body

    async def me(self, token: str) -> dict[str, Any]:
        return await self._request(
            "GET", "me", token, params={"fields": "user_id,username,account_type"}
        )

    @staticmethod
    def _message(text: str, button: Button | None) -> dict[str, Any]:
        if button is None:
            return {"text": text}
        wire: dict[str, str]
        if isinstance(button, PostbackButton):
            wire = {"type": "postback", "title": button.title, "payload": button.payload}
        else:
            wire = {"type": "web_url", "url": button.url, "title": button.title}
        return {
            "attachment": {
                "type": "template",
                "payload": {"template_type": "button", "text": text, "buttons": [wire]},
            }
        }

    async def _send(self, token: str, recipient: dict[str, str], message: dict[str, Any]) -> str:
        body = await self._request(
            "POST", "me/messages", token, json={"recipient": recipient, "message": message}
        )
        message_id = body.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise AmbiguousFailure("2xx response without message_id")
        return message_id

    async def send_private_reply(
        self, token: str, comment_id: str, text: str, button: Button | None = None
    ) -> str:
        """Send the one private reply allowed for this comment. Returns Meta's message id."""
        return await self._send(token, {"comment_id": comment_id}, self._message(text, button))

    async def send_message(
        self,
        token: str,
        recipient_id: str,
        text: str,
        button: Button | None = None,
        quick_replies: list[QuickReply] | None = None,
    ) -> str:
        """Message someone who wrote to us first, by user id. Only valid inside the 24 h window."""
        message = self._message(text, button)
        if quick_replies:
            message["quick_replies"] = [
                {"content_type": "text", "title": q.title, "payload": q.payload}
                for q in quick_replies
            ]
        return await self._send(token, {"id": recipient_id}, message)

    async def get_profile(self, token: str, user_id: str, fields: str) -> dict[str, Any]:
        """User Profile API. Meta only answers for people who have messaged the account."""
        return await self._request("GET", user_id, token, params={"fields": fields})

    async def reply_to_comment(self, token: str, comment_id: str, text: str) -> str:
        body = await self._request("POST", f"{comment_id}/replies", token, json={"message": text})
        reply_id = body.get("id")
        if not isinstance(reply_id, str) or not reply_id:
            raise AmbiguousFailure("2xx response without reply id")
        return reply_id

    async def list_media(self, token: str, limit: int = 10) -> list[dict[str, Any]]:
        fields = "id,timestamp,permalink,caption,media_type,media_url,thumbnail_url"
        params = {"fields": fields, "limit": str(limit)}
        body = await self._request("GET", "me/media", token, params=params)
        return list(body.get("data", []))

    async def list_comments(
        self, token: str, media_id: str, *, limit: int = 50, after: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        params = {"fields": "id,text,timestamp,from,replies{from}", "limit": str(limit)}
        if after:
            params["after"] = after
        body = await self._request("GET", f"{media_id}/comments", token, params=params)
        cursor = body.get("paging", {}).get("cursors", {}).get("after")
        has_next = bool(body.get("paging", {}).get("next"))
        return list(body.get("data", [])), (cursor if has_next else None)

    async def get(self, token: str, path: str, params: dict[str, str]) -> dict[str, Any]:
        """Read-only GET for the insights sync, which builds its own fields and metrics."""
        return await self._request("GET", path, token, params=params)

    async def list_media_page(
        self, token: str, fields: str, *, limit: int = 50, after: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        params = {"fields": fields, "limit": str(limit)}
        if after:
            params["after"] = after
        body = await self._request("GET", "me/media", token, params=params)
        cursor = body.get("paging", {}).get("cursors", {}).get("after")
        has_next = bool(body.get("paging", {}).get("next"))
        return list(body.get("data", [])), (cursor if has_next else None)

    async def subscribe(self, token: str, fields: str) -> dict[str, Any]:
        return await self._request(
            "POST", "me/subscribed_apps", token, params={"subscribed_fields": fields}
        )

    async def refresh_token(self, token: str) -> RefreshedToken:
        # This endpoint only takes the token in the query string. It is never logged:
        # httpx logs are off and our errors never carry URLs.
        body = await self._request(
            "GET",
            "refresh_access_token",
            token,
            params={"grant_type": "ig_refresh_token", "access_token": token},
            versioned=False,
            auth_header=False,
        )
        new_token = body.get("access_token")
        expires_in = body.get("expires_in")
        if not isinstance(new_token, str) or not isinstance(expires_in, int):
            raise AmbiguousFailure("refresh response without access_token/expires_in")
        return RefreshedToken(access_token=new_token, expires_in_seconds=expires_in)
