import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app.meta.client import GRAPH_HOST, LinkButton, MetaClient
from app.meta.errors import (
    AmbiguousFailure,
    ConfirmedRejection,
    NotSent,
    RejectionKind,
    classify_error_response,
)

TOKEN = "IGAAtesttoken0123456789"
SEND = f"{GRAPH_HOST}/v25.0/me/messages"


@pytest.fixture
async def meta() -> AsyncIterator[MetaClient]:
    async with httpx.AsyncClient() as http:
        yield MetaClient(http, "v25.0")


def graph_error(code: int, message: str = "x", subcode: int | None = None) -> dict[str, object]:
    error: dict[str, object] = {"message": message, "type": "OAuthException", "code": code}
    if subcode is not None:
        error["error_subcode"] = subcode
    return {"error": error}


# ---- classification table ----


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (401, 190, RejectionKind.TOKEN_INVALID),
        (400, 4, RejectionKind.RATE_LIMITED),
        (400, 17, RejectionKind.RATE_LIMITED),
        (400, 368, RejectionKind.RATE_LIMITED),
        (403, 10, RejectionKind.PERMANENT),
        (400, 100, RejectionKind.PERMANENT),
        (403, 200, RejectionKind.PERMANENT),
        (400, 551, RejectionKind.PERMANENT),
        (400, 9999, RejectionKind.PERMANENT),
    ],
)
def test_explicit_errors_are_confirmed_rejections(
    status: int, code: int, expected: RejectionKind
) -> None:
    error = classify_error_response(status, graph_error(code))
    assert isinstance(error, ConfirmedRejection)
    assert error.kind == expected
    assert error.code == code


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, graph_error(1)),
        (400, graph_error(1)),
        (400, graph_error(2)),
        (503, graph_error(100)),
        (502, None),
        (400, {"unexpected": True}),
    ],
)
def test_unclear_errors_are_ambiguous(status: int, body: object) -> None:
    assert isinstance(classify_error_response(status, body), AmbiguousFailure)


def test_error_message_never_carries_tokens() -> None:
    error = classify_error_response(
        400, graph_error(100, "bad url https://x.com/a?access_token=IGAAsecret")
    )
    assert "IGAAsecret" not in str(error)


# ---- client behaviour ----


@respx.mock
async def test_private_reply_uses_bearer_header_and_comment_recipient(meta: MetaClient) -> None:
    route = respx.post(SEND).mock(
        return_value=httpx.Response(200, json={"recipient_id": "1", "message_id": "m_1"})
    )
    message_id = await meta.send_private_reply(TOKEN, "c_1", "oi")
    assert message_id == "m_1"
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)
    assert json.loads(request.content) == {
        "recipient": {"comment_id": "c_1"},
        "message": {"text": "oi"},
    }


@respx.mock
async def test_private_reply_with_button_template(meta: MetaClient) -> None:
    route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m_2"}))
    await meta.send_private_reply(TOKEN, "c_1", "oi", LinkButton("Abrir", "https://x.com/a"))
    payload = json.loads(route.calls.last.request.content)["message"]["attachment"]["payload"]
    assert payload["template_type"] == "button"
    assert payload["buttons"] == [{"type": "web_url", "url": "https://x.com/a", "title": "Abrir"}]


@respx.mock
async def test_success_without_message_id_is_ambiguous(meta: MetaClient) -> None:
    respx.post(SEND).mock(return_value=httpx.Response(200, json={"recipient_id": "1"}))
    with pytest.raises(AmbiguousFailure):
        await meta.send_private_reply(TOKEN, "c_1", "oi")


@respx.mock
async def test_read_timeout_is_ambiguous(meta: MetaClient) -> None:
    respx.post(SEND).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AmbiguousFailure):
        await meta.send_private_reply(TOKEN, "c_1", "oi")


@respx.mock
async def test_connect_error_is_not_sent(meta: MetaClient) -> None:
    respx.post(SEND).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(NotSent):
        await meta.send_private_reply(TOKEN, "c_1", "oi")


@respx.mock
async def test_token_error_is_confirmed(meta: MetaClient) -> None:
    respx.post(SEND).mock(return_value=httpx.Response(401, json=graph_error(190)))
    with pytest.raises(ConfirmedRejection) as excinfo:
        await meta.send_private_reply(TOKEN, "c_1", "oi")
    assert excinfo.value.kind == RejectionKind.TOKEN_INVALID


@respx.mock
async def test_refresh_token_error_does_not_leak_the_token(meta: MetaClient) -> None:
    respx.get(f"{GRAPH_HOST}/refresh_access_token").mock(
        return_value=httpx.Response(400, json=graph_error(190, "Error validating access token"))
    )
    with pytest.raises(ConfirmedRejection) as excinfo:
        await meta.refresh_token(TOKEN)
    assert TOKEN not in str(excinfo.value)


@respx.mock
async def test_list_comments_paginates(meta: MetaClient) -> None:
    respx.get(f"{GRAPH_HOST}/v25.0/media_1/comments").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"id": "c1"}],
                "paging": {"cursors": {"after": "CUR"}, "next": "https://..."},
            },
        )
    )
    comments, cursor = await meta.list_comments(TOKEN, "media_1")
    assert comments == [{"id": "c1"}]
    assert cursor == "CUR"
