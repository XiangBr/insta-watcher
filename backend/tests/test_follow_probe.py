"""The probes that answer, before the follow gate exists, what Meta really returns."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from app.cli import main
from app.config import get_settings
from app.dispatcher import message_events
from app.meta.client import GRAPH_HOST, SUBSCRIBED_FIELDS, LinkButton, MetaClient, PostbackButton
from app.status import summarize_webhook_payload
from app.tokens import cipher_for
from tests.conftest import strong_secrets

TS = "2026-10-02T12:00:00.000000Z"
TOKEN = "IGAAprobetoken" + "p" * 40
USER_ID = "28542923325365400"
PROFILE = f"{GRAPH_HOST}/v25.0/{USER_ID}"
SEND = f"{GRAPH_HOST}/v25.0/me/messages"


# ---- the client ----------------------------------------------------------------------------


def test_subscribed_fields_include_postbacks() -> None:
    assert set(SUBSCRIBED_FIELDS.split(",")) == {"comments", "messages", "messaging_postbacks"}


@respx.mock
async def test_a_postback_button_goes_out_as_a_postback() -> None:
    route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m1"}))
    async with httpx.AsyncClient() as http:
        meta = MetaClient(http, "v25.0")
        await meta.send_message(TOKEN, USER_ID, "Já seguiu?", PostbackButton("Já segui", "bw:x"))
        await meta.send_message(TOKEN, USER_ID, "Veja", LinkButton("Abrir", "https://a.test"))
    postback, link = (json.loads(call.request.content) for call in route.calls)
    assert postback["message"]["attachment"]["payload"]["buttons"] == [
        {"type": "postback", "title": "Já segui", "payload": "bw:x"}
    ]
    assert link["message"]["attachment"]["payload"]["buttons"] == [
        {"type": "web_url", "url": "https://a.test", "title": "Abrir"}
    ]


# ---- how postbacks show up in the webhook log ----------------------------------------------


def postback_payload(shape: str, payload: str = "bw:probe:7") -> dict[str, object]:
    event = {
        "sender": {"id": USER_ID},
        "recipient": {"id": "17841400000000001"},
        "timestamp": 1790910591,
        "postback": {"mid": "mid_pb", "title": "Já segui", "payload": payload},
    }
    entry: dict[str, object] = {"id": "17841400000000001", "time": 1790910591}
    if shape == "messaging":
        entry["messaging"] = [event]
    else:
        entry["changes"] = [{"field": "messaging_postbacks", "value": event}]
    return {"object": "instagram", "entry": [entry]}


@pytest.mark.parametrize("shape", ["messaging", "changes"])
def test_a_postback_is_listed_once_with_its_payload(shape: str) -> None:
    summary = summarize_webhook_payload(postback_payload(shape), with_text=False)
    assert summary == [
        {"field": "messaging_postbacks", "kind": "postback", "payload": "bw:probe:7"}
    ]
    assert len(list(message_events(postback_payload(shape)))) == 1


def test_a_long_payload_is_cut_and_nothing_personal_is_shown() -> None:
    summary = summarize_webhook_payload(postback_payload("messaging", "x" * 500), with_text=False)
    assert len(summary[0]["payload"]) == 100
    assert USER_ID not in json.dumps(summary)


# ---- the CLI probes ------------------------------------------------------------------------


@pytest.fixture
def seeded(monkeypatch: pytest.MonkeyPatch, database_url: str) -> Iterator[Path]:
    for name, value in strong_secrets().items():
        monkeypatch.setenv(name.upper(), value)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    path = Path(database_url.removeprefix("sqlite+aiosqlite:///"))
    blob = cipher_for(get_settings()).encrypt(TOKEN)
    con = sqlite3.connect(path)
    con.execute(
        "insert into account (id, ig_user_id, username, token_encrypted, token_set_at,"
        " token_expiry_confirmed, status, sending_paused, updated_at)"
        " values (1, '17841400000000001', 'dono', ?, ?, 0, 'ACTIVE', 0, ?)",
        (blob, TS, TS),
    )
    con.execute(
        "insert into campaign (name, is_active, match_any_post, keywords, match_mode,"
        " dm_message, public_reply_enabled, public_reply_messages, created_at, updated_at)"
        " values ('c', 1, 0, '[\"x\"]', 'WHOLE_WORD', 'Oi', 0, '[]', ?, ?)",
        (TS, TS),
    )
    con.execute(
        "insert into delivery (id, campaign_id, trigger, origin, source_id, actor_id, status,"
        " attempts, created_at, updated_at)"
        " values (7, 1, 'STORY_REPLY', 'WEBHOOK', 'mid_1', ?, 'SENT', 1, ?, ?)",
        (USER_ID, TS, TS),
    )
    con.execute(
        "insert into delivery (id, campaign_id, trigger, origin, source_id, actor_id, status,"
        " attempts, anonymized_at, created_at, updated_at)"
        " values (8, 1, 'COMMENT', 'WEBHOOK', 'anon:8', 'anon', 'SENT', 1, ?, ?, ?)",
        (TS, TS, TS),
    )
    con.commit()
    con.close()
    yield path
    get_settings.cache_clear()


@respx.mock
def test_follow_status_shows_the_raw_answer(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    route = respx.get(PROFILE).mock(
        return_value=httpx.Response(
            200, json={"username": "ana", "is_user_follow_business": True, "id": USER_ID}
        )
    )
    assert main(["follow-status", "7"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "delivery": 7,
        "trigger": "STORY_REPLY",
        "username": "ana",
        "is_user_follow_business": True,
        "is_business_follow_user": "(absent)",  # Meta left the key out: not the same as null
    }
    assert route.calls.last.request.url.params["fields"] == (
        "username,is_user_follow_business,is_business_follow_user"
    )
    assert route.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
def test_follow_status_keeps_an_explicit_null(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.get(PROFILE).mock(
        return_value=httpx.Response(200, json={"username": "ana", "is_user_follow_business": None})
    )
    assert main(["follow-status", "7"]) == 0
    assert json.loads(capsys.readouterr().out)["is_user_follow_business"] is None


@respx.mock
def test_follow_status_reports_a_refusal_without_a_stack_trace(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.get(PROFILE).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Consent not given", "code": 230}}
        )
    )
    assert main(["follow-status", "7"]) == 1
    out = capsys.readouterr().out
    assert "Meta refused" in out
    assert "Consent not given" in out
    assert TOKEN not in out


def test_the_probes_refuse_a_missing_or_anonymized_delivery(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["follow-status", "99"]) == 1
    assert "not found" in capsys.readouterr().out
    assert main(["probe-postback", "8"]) == 1
    assert "past retention" in capsys.readouterr().out


@respx.mock
def test_probe_postback_sends_a_tappable_button_to_the_user_id(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    route = respx.post(SEND).mock(return_value=httpx.Response(200, json={"message_id": "m_out"}))
    assert main(["probe-postback", "7"]) == 0
    body = json.loads(route.calls.last.request.content)
    assert body["recipient"] == {"id": USER_ID}
    button = body["message"]["attachment"]["payload"]["buttons"][0]
    assert button == {"type": "postback", "title": "Já segui", "payload": "bw:probe:7"}
    assert "m_out" in capsys.readouterr().out


@respx.mock
def test_probe_postback_explains_a_refusal(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.post(SEND).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Outside the allowed window", "code": 10}}
        )
    )
    assert main(["probe-postback", "7"]) == 1
    assert "Outside the allowed window" in capsys.readouterr().out
