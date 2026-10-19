"""Chat: direct messages through flows, quick replies, follow-ups, human mode and the AI."""

import json
import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat_worker import ChatWorker, recover_stale
from app.config import Settings
from app.db import utcnow
from app.dispatcher import dispatch_pending
from app.flows import ChatConfig, FlowInput, option_payload, parse_option_payload, save_chat_config
from app.maintenance import apply_retention
from app.matching import KeywordMatcher
from app.meta.client import GRAPH_HOST, MetaClient
from app.models import (
    Account,
    ChatMessage,
    ChatOutbox,
    ChatSource,
    Contact,
    Delivery,
    Flow,
    Job,
    JobType,
    OutboxKind,
    OutboxStatus,
    Trigger,
)
from app.openrouter import OPENROUTER_HOST, OpenRouterClient
from app.tokens import cipher_for
from tests.conftest import strong_secrets
from tests.factories import FAN_ID, OWNER_ID, add_campaign, add_webhook_event

TOKEN = "IGAAchattoken" + "c" * 40
SEND = f"{GRAPH_HOST}/v25.0/me/messages"
CHAT = f"{OPENROUTER_HOST}/api/v1/chat/completions"
OPENROUTER_KEY = "sk-or-v1-" + secrets.token_hex(32)
matcher = KeywordMatcher()


def dm(
    mid: str,
    text: str | None = "oi",
    *,
    sender: str = FAN_ID,
    quick_reply: str | None = None,
    echo: bool = False,
) -> dict[str, Any]:
    message: dict[str, Any] = {"mid": mid}
    if text is not None:
        message["text"] = text
    if quick_reply:
        message["quick_reply"] = {"payload": quick_reply}
    if echo:
        message["is_echo"] = True
    event = {
        "sender": {"id": OWNER_ID if echo else sender},
        "recipient": {"id": sender if echo else OWNER_ID},
        "timestamp": 1790910591,
        "message": message,
    }
    return {"object": "instagram", "entry": [{"id": OWNER_ID, "messaging": [event]}]}


def flow_body(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Menu",
        "keywords": ["menu"],
        "steps": [
            {
                "id": "start",
                "message": "Oi {username}! O que você quer?",
                "tags": ["lead"],
                "options": [
                    {"label": "Curso", "action": "step", "step": "curso", "tags": ["curso"]},
                    {"label": "Material", "action": "link", "url": "https://exemplo.com/pdf"},
                    {"label": "Falar com alguém", "action": "handoff"},
                ],
            },
            {
                "id": "curso",
                "message": "As inscrições abrem segunda.",
                "followup": {"delay_minutes": 30, "message": "Ficou alguma dúvida?"},
            },
        ],
        **overrides,
    }


async def add_flow(sf: async_sessionmaker[AsyncSession], **overrides: Any) -> Flow:
    from app.flows import apply_flow

    async with sf() as session, session.begin():
        flow = Flow()
        apply_flow(flow, FlowInput.model_validate(flow_body(**overrides)))
        session.add(flow)
    return flow


async def set_config(sf: async_sessionmaker[AsyncSession], **values: Any) -> None:
    async with sf() as session, session.begin():
        await save_chat_config(session, ChatConfig(**values))


@pytest.fixture
def keyed_settings(database_url: str) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=database_url,
        openrouter_api_key=OPENROUTER_KEY,
        **strong_secrets(),
    )


@pytest.fixture
async def chat_app(keyed_settings: Settings) -> AsyncIterator[FastAPI]:
    from app.factory import create_app

    application = create_app(keyed_settings)
    async with application.router.lifespan_context(application):
        async with application.state.session_factory() as session, session.begin():
            session.add(
                Account(
                    id=1,
                    ig_user_id=OWNER_ID,
                    username="dono",
                    token_encrypted=cipher_for(keyed_settings).encrypt(TOKEN),
                    token_set_at=utcnow(),
                )
            )
        yield application


@pytest.fixture
async def api(chat_app: FastAPI, keyed_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    token = keyed_settings.api_admin_token.get_secret_value()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=chat_app),
        base_url="http://test",
        headers={"authorization": f"Bearer {token}"},
    ) as c:
        yield c


def sf_of(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    return factory


async def receive(app: FastAPI, payload: dict[str, Any], *, ai: bool = False) -> int:
    await add_webhook_event(sf_of(app), payload)
    return await dispatch_pending(sf_of(app), matcher, ai_available=ai)


async def all_rows(app: FastAPI, model: Any) -> list[Any]:
    async with sf_of(app)() as session:
        return list(await session.scalars(select(model)))


async def make_due(app: FastAPI) -> None:
    async with sf_of(app)() as session, session.begin():
        await session.execute(
            update(ChatOutbox)
            .where(ChatOutbox.status == OutboxStatus.PENDING)
            .values(run_at=utcnow() - timedelta(seconds=1))
        )


def worker(app: FastAPI, settings: Settings, http: httpx.AsyncClient) -> ChatWorker:
    return ChatWorker(
        sf_of(app), settings, MetaClient(http, "v25.0"), OpenRouterClient(http, OPENROUTER_KEY)
    )


PROFILE = f"{GRAPH_HOST}/v25.0/{FAN_ID}"


@pytest.fixture(autouse=True)
def _profile_lookup(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(PROFILE).mock(return_value=httpx.Response(200, json={"username": "fan"}))


def sent_ok(mid: str = "m_out") -> httpx.Response:
    return httpx.Response(200, json={"recipient_id": FAN_ID, "message_id": mid})


# ---- validation --------------------------------------------------------------------------


def test_flow_rules() -> None:
    FlowInput.model_validate(flow_body())
    bad_target = flow_body()
    bad_target["steps"][0]["options"][0]["step"] = "nope"
    with pytest.raises(ValidationError, match="unknown step"):
        FlowInput.model_validate(bad_target)
    both = flow_body()
    both["steps"][0]["link_url"] = "https://exemplo.com"
    with pytest.raises(ValidationError, match="not both"):
        FlowInput.model_validate(both)
    dup = flow_body()
    dup["steps"][1]["id"] = "start"
    with pytest.raises(ValidationError, match="unique"):
        FlowInput.model_validate(dup)
    insecure = flow_body()
    insecure["steps"][0]["options"][1]["url"] = "http://exemplo.com"
    with pytest.raises(ValidationError, match="https"):
        FlowInput.model_validate(insecure)


def test_option_payload_round_trip() -> None:
    assert parse_option_payload(option_payload(7, "start", 2)) == (7, "start", 2)
    assert parse_option_payload("bw:gate:1") is None
    assert parse_option_payload("bwf:7:START:2") is None
    assert parse_option_payload(None) is None


# ---- flows -------------------------------------------------------------------------------


@respx.mock
async def test_keyword_starts_a_flow_and_options_walk_it(
    chat_app: FastAPI, keyed_settings: Settings
) -> None:
    flow = await add_flow(sf_of(chat_app))
    assert await receive(chat_app, dm("m1", "quero o MENU")) == 1
    send = respx.post(SEND).mock(side_effect=[sent_ok("o1"), sent_ok("o2"), sent_ok("o3")])
    async with httpx.AsyncClient() as http:
        w = worker(chat_app, keyed_settings, http)
        await w.run_until_idle()
        body = json.loads(send.calls[0].request.content)
        assert body["recipient"] == {"id": FAN_ID}
        assert body["message"]["text"] == "Oi fan! O que você quer?"
        replies = body["message"]["quick_replies"]
        assert [r["title"] for r in replies] == ["Curso", "Material", "Falar com alguém"]
        assert replies[0]["payload"] == option_payload(flow.id, "start", 0)

        # The tap on "Curso" goes to the next step and tags the person.
        await receive(chat_app, dm("m2", "Curso", quick_reply=replies[0]["payload"]))
        await w.run_until_idle()
        assert json.loads(send.calls[1].request.content)["message"] == {
            "text": "As inscrições abrem segunda."
        }
        # The tap on "Material" sends the link as a button.
        await receive(chat_app, dm("m3", "Material", quick_reply=replies[1]["payload"]))
        await w.run_until_idle()
        link = json.loads(send.calls[2].request.content)["message"]["attachment"]["payload"]
        assert link["buttons"][0]["url"] == "https://exemplo.com/pdf"

    [contact] = await all_rows(chat_app, Contact)
    assert contact.tags == ["lead", "curso"]
    messages = await all_rows(chat_app, ChatMessage)
    assert [m.source for m in messages] == [
        ChatSource.USER,
        ChatSource.FLOW,
        ChatSource.USER,
        ChatSource.FLOW,
        ChatSource.USER,
        ChatSource.FLOW,
    ]
    followups = [o for o in await all_rows(chat_app, ChatOutbox) if o.kind == OutboxKind.FOLLOWUP]
    assert len(followups) == 1 and followups[0].status == OutboxStatus.PENDING
    # ...and it is skipped, because the person wrote after that step (the Material tap).
    await make_due(chat_app)
    async with httpx.AsyncClient() as http:
        await worker(chat_app, keyed_settings, http).run_until_idle()
    [followup] = [o for o in await all_rows(chat_app, ChatOutbox) if o.kind == OutboxKind.FOLLOWUP]
    assert followup.status == OutboxStatus.CANCELLED
    assert followup.error == "the person wrote after the step"


@respx.mock
async def test_followup_goes_out_when_the_person_is_quiet(
    chat_app: FastAPI, keyed_settings: Settings
) -> None:
    await add_flow(sf_of(chat_app), keywords=["curso"], steps=flow_body()["steps"][1:])
    await receive(chat_app, dm("m1", "curso"))
    send = respx.post(SEND).mock(side_effect=[sent_ok("o1"), sent_ok("o2")])
    async with httpx.AsyncClient() as http:
        w = worker(chat_app, keyed_settings, http)
        await w.run_until_idle()
        await make_due(chat_app)
        await w.run_until_idle()
    assert json.loads(send.calls[1].request.content)["message"]["text"] == "Ficou alguma dúvida?"
    sources = [m.source for m in await all_rows(chat_app, ChatMessage)]
    assert sources == [ChatSource.USER, ChatSource.FLOW, ChatSource.FOLLOWUP]


async def test_duplicate_webhook_queues_once(chat_app: FastAPI) -> None:
    await add_flow(sf_of(chat_app))
    assert await receive(chat_app, dm("m1", "menu")) == 1
    assert await receive(chat_app, dm("m1", "menu")) == 0
    assert len(await all_rows(chat_app, ChatOutbox)) == 1
    assert len(await all_rows(chat_app, ChatMessage)) == 1


async def test_welcome_flow_answers_only_the_first_message(chat_app: FastAPI) -> None:
    flow = await add_flow(sf_of(chat_app), keywords=[])
    await set_config(sf_of(chat_app), welcome_flow_id=flow.id)
    assert await receive(chat_app, dm("m1", "bom dia")) == 1
    assert await receive(chat_app, dm("m2", "tudo bem?")) == 0


@respx.mock
async def test_window_closed_cancels(chat_app: FastAPI, keyed_settings: Settings) -> None:
    await add_flow(sf_of(chat_app))
    await receive(chat_app, dm("m1", "menu"))
    async with sf_of(chat_app)() as session, session.begin():
        await session.execute(
            update(Contact).values(last_inbound_at=utcnow() - timedelta(hours=25))
        )
    send = respx.post(SEND).mock(return_value=sent_ok())
    async with httpx.AsyncClient() as http:
        await worker(chat_app, keyed_settings, http).run_until_idle()
    assert not send.called
    [row] = await all_rows(chat_app, ChatOutbox)
    assert row.status == OutboxStatus.CANCELLED and "window" in (row.error or "")


# ---- campaigns by direct message ---------------------------------------------------------


async def test_direct_message_campaign_wins_over_flows(chat_app: FastAPI) -> None:
    await add_campaign(
        sf_of(chat_app),
        post_id=None,
        trigger_comments=False,
        trigger_messages=True,
        keywords=["link"],
    )
    await add_flow(sf_of(chat_app), keywords=["link"])
    assert await receive(chat_app, dm("m1", "me manda o link")) == 1
    [delivery] = await all_rows(chat_app, Delivery)
    [job] = await all_rows(chat_app, Job)
    assert delivery.trigger == Trigger.MESSAGE and delivery.source_id == "m1"
    assert job.type == JobType.DIRECT_MESSAGE
    assert await all_rows(chat_app, ChatOutbox) == []


# ---- human mode --------------------------------------------------------------------------


@respx.mock
async def test_handoff_option_pauses_everything(
    chat_app: FastAPI, keyed_settings: Settings
) -> None:
    flow = await add_flow(sf_of(chat_app))
    await set_config(sf_of(chat_app), handoff_message="Já te chamo alguém.")
    await receive(chat_app, dm("m1", "Falar", quick_reply=option_payload(flow.id, "start", 2)))
    send = respx.post(SEND).mock(return_value=sent_ok("o1"))
    async with httpx.AsyncClient() as http:
        await worker(chat_app, keyed_settings, http).run_until_idle()
    assert json.loads(send.calls[0].request.content)["message"]["text"] == "Já te chamo alguém."
    [contact] = await all_rows(chat_app, Contact)
    assert contact.human_until is not None and contact.human_reason == "handoff_option"
    # While a human has the conversation, even the flow keyword gets nothing.
    assert await receive(chat_app, dm("m2", "menu")) == 0


async def test_manual_reply_in_the_app_pauses_and_our_echo_does_not(chat_app: FastAPI) -> None:
    await add_flow(sf_of(chat_app))
    await receive(chat_app, dm("m1", "oi"))
    # Echo of a message we sent from the outbox: recognized by its message id.
    [contact] = await all_rows(chat_app, Contact)
    async with sf_of(chat_app)() as session, session.begin():
        session.add(
            ChatOutbox(
                contact_id=contact.id,
                kind=OutboxKind.STEP,
                payload={},
                dedupe_key="x",
                status=OutboxStatus.SENT,
                sent_at=utcnow() - timedelta(hours=1),
                message_id="ours",
            )
        )
    await receive(chat_app, dm("ours", "Oi! O que você quer?", echo=True))
    [contact] = await all_rows(chat_app, Contact)
    assert contact.human_until is None
    # A message typed in the Instagram app: unknown id, nothing of ours in flight.
    await receive(chat_app, dm("typed", "Opa, sou eu aqui", echo=True))
    [contact] = await all_rows(chat_app, Contact)
    assert contact.human_until is not None and contact.human_reason == "manual_reply"
    sources = {m.mid: m.source for m in await all_rows(chat_app, ChatMessage)}
    assert sources["typed"] == ChatSource.OWNER
    assert await receive(chat_app, dm("m2", "menu")) == 0


# ---- AI ----------------------------------------------------------------------------------


def ai_answer(reply: str, handoff: bool = False, cost: float = 0.001) -> httpx.Response:
    content = json.dumps({"reply": reply, "handoff": handoff})
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}}], "usage": {"cost": cost}}
    )


@respx.mock
async def test_ai_answers_what_nothing_else_matched(
    chat_app: FastAPI, keyed_settings: Settings
) -> None:
    await add_flow(sf_of(chat_app))
    await set_config(sf_of(chat_app), ai_enabled=True, knowledge="O curso custa R$ 97.")
    # Off without the key, and the AI waits a few seconds for a burst of messages.
    assert await receive(chat_app, dm("m1", "quanto custa?"), ai=False) == 0
    assert await receive(chat_app, dm("m2", "e tem certificado?"), ai=True) == 1
    assert await receive(chat_app, dm("m3", "?"), ai=True) == 0  # same pending answer
    [pending] = await all_rows(chat_app, ChatOutbox)
    assert pending.kind == OutboxKind.AI_REPLY and pending.run_at > utcnow()

    model = respx.post(CHAT).mock(return_value=ai_answer("Custa R$ 97 e tem certificado!"))
    send = respx.post(SEND).mock(return_value=sent_ok("o1"))
    await make_due(chat_app)
    async with httpx.AsyncClient() as http:
        await worker(chat_app, keyed_settings, http).run_until_idle()
    request = json.loads(model.calls[0].request.content)
    assert "R$ 97" in request["messages"][0]["content"]
    assert "menu" in request["messages"][0]["content"]  # the flows it may point to
    assert [m["content"] for m in request["messages"][1:]] == [
        "quanto custa?",
        "e tem certificado?",
        "?",
    ]
    assert json.loads(send.calls[0].request.content)["message"]["text"] == (
        "Custa R$ 97 e tem certificado!"
    )
    [out] = [m for m in await all_rows(chat_app, ChatMessage) if m.source == ChatSource.AI]
    assert out.cost_usd == 0.001


@respx.mock
async def test_ai_handoff_and_unreadable_answers_go_to_a_human(
    chat_app: FastAPI, keyed_settings: Settings
) -> None:
    await set_config(sf_of(chat_app), ai_enabled=True, handoff_message="Vou chamar alguém.")
    await receive(chat_app, dm("m1", "quero negociar"), ai=True)
    respx.post(CHAT).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "oi"}}]})
    )
    send = respx.post(SEND).mock(return_value=sent_ok("o1"))
    await make_due(chat_app)
    async with httpx.AsyncClient() as http:
        await worker(chat_app, keyed_settings, http).run_until_idle()
    assert json.loads(send.calls[0].request.content)["message"]["text"] == "Vou chamar alguém."
    [contact] = await all_rows(chat_app, Contact)
    assert contact.human_reason == "ai_handoff"


@respx.mock
async def test_ai_per_person_limit_hands_off(chat_app: FastAPI, keyed_settings: Settings) -> None:
    await set_config(sf_of(chat_app), ai_enabled=True, ai_max_per_contact_day=1)
    model = respx.post(CHAT).mock(return_value=ai_answer("Resposta"))
    respx.post(SEND).mock(side_effect=[sent_ok("o1"), sent_ok("o2")])
    async with httpx.AsyncClient() as http:
        w = worker(chat_app, keyed_settings, http)
        for mid in ("m1", "m2"):
            await receive(chat_app, dm(mid, "pergunta"), ai=True)
            await make_due(chat_app)
            await w.run_until_idle()
    assert model.call_count == 1
    sources = [m.source for m in await all_rows(chat_app, ChatMessage) if m.direction == "OUT"]
    assert sources == [ChatSource.AI, ChatSource.HANDOFF]


# ---- safety ------------------------------------------------------------------------------


@respx.mock
async def test_ambiguous_send_is_unconfirmed_and_never_resent(
    chat_app: FastAPI, keyed_settings: Settings
) -> None:
    await add_flow(sf_of(chat_app))
    await receive(chat_app, dm("m1", "menu"))
    send = respx.post(SEND).mock(return_value=httpx.Response(500, json={"error": {"code": 2}}))
    async with httpx.AsyncClient() as http:
        w = worker(chat_app, keyed_settings, http)
        await w.run_until_idle()
        await make_due(chat_app)
        await w.run_until_idle()
    assert send.call_count == 1
    [row] = await all_rows(chat_app, ChatOutbox)
    assert row.status == OutboxStatus.UNCONFIRMED


async def test_crash_recovery_uses_send_started_at(chat_app: FastAPI) -> None:
    await add_flow(sf_of(chat_app))
    await receive(chat_app, dm("m1", "menu"))
    await receive(chat_app, dm("m2", "menu", sender="99999"))
    async with sf_of(chat_app)() as session, session.begin():
        rows = list(await session.scalars(select(ChatOutbox).order_by(ChatOutbox.id)))
        rows[0].status = rows[1].status = OutboxStatus.RUNNING
        rows[1].send_started_at = utcnow()
    await recover_stale(sf_of(chat_app), at_boot=True)
    rows = sorted(await all_rows(chat_app, ChatOutbox), key=lambda r: r.id)
    assert [r.status for r in rows] == [OutboxStatus.PENDING, OutboxStatus.UNCONFIRMED]


async def test_retention_clears_chat_text(chat_app: FastAPI, keyed_settings: Settings) -> None:
    await receive(chat_app, dm("m1", "meu telefone é 9999"))
    later = utcnow() + timedelta(days=keyed_settings.retention_delivery_days + 1)
    await apply_retention(sf_of(chat_app), keyed_settings, now=later)
    [message] = await all_rows(chat_app, ChatMessage)
    [contact] = await all_rows(chat_app, Contact)
    assert message.text is None
    assert contact.ig_user_id.startswith("anon:") and contact.anonymized_at is not None


# ---- API ---------------------------------------------------------------------------------


async def test_flow_api(api: httpx.AsyncClient) -> None:
    created = await api.post("/api/v1/chat/flows", json=flow_body())
    assert created.status_code == 201, created.text
    flow_id = created.json()["id"]
    clash = await api.post("/api/v1/chat/flows", json=flow_body(name="Outro"))
    assert clash.status_code == 409
    bad = flow_body()
    bad["steps"][0]["options"][0]["step"] = "nope"
    assert (await api.post("/api/v1/chat/flows", json=bad)).status_code == 422
    updated = await api.put(f"/api/v1/chat/flows/{flow_id}", json=flow_body(name="Menu 2"))
    assert updated.json()["name"] == "Menu 2"
    assert [f["name"] for f in (await api.get("/api/v1/chat/flows")).json()] == ["Menu 2"]
    assert (await api.delete(f"/api/v1/chat/flows/{flow_id}")).status_code == 204
    assert (await api.get(f"/api/v1/chat/flows/{flow_id}")).status_code == 404


async def test_config_api(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/v1/chat/config")).json()
    assert body["config"]["ai_enabled"] is False and body["ai_available"] is True
    config = {**body["config"], "ai_enabled": True, "knowledge": "Horário: 9h às 18h."}
    saved = await api.put("/api/v1/chat/config", json=config)
    assert saved.json()["config"]["knowledge"] == "Horário: 9h às 18h."
    missing = await api.put("/api/v1/chat/config", json={**config, "welcome_flow_id": 999})
    assert missing.status_code == 404


async def test_ai_switch_needs_the_key(client: httpx.AsyncClient, settings: Settings) -> None:
    client.headers["authorization"] = f"Bearer {settings.api_admin_token.get_secret_value()}"
    refused = await client.put("/api/v1/chat/config", json={"ai_enabled": True})
    assert refused.status_code == 409
    assert (await client.post("/api/v1/chat/ai/test", json={"message": "oi"})).status_code == 409


@respx.mock
async def test_ai_test_sends_nothing(api: httpx.AsyncClient, chat_app: FastAPI) -> None:
    respx.post(CHAT).mock(return_value=ai_answer("Abrimos às 9h!", cost=0.0004))
    send = respx.post(SEND).mock(return_value=sent_ok())
    result = await api.post("/api/v1/chat/ai/test", json={"message": "que horas abre?"})
    assert result.json()["sent_text"] == "Abrimos às 9h!" and result.json()["cost_usd"] == 0.0004
    assert not send.called
    assert await all_rows(chat_app, ChatMessage) == []


async def test_contacts_api_tags_human_and_reply(api: httpx.AsyncClient, chat_app: FastAPI) -> None:
    await receive(chat_app, dm("m1", "oi"))
    page = (await api.get("/api/v1/chat/contacts")).json()
    assert page["summary"]["open_windows"] == 1
    [contact] = page["contacts"]
    assert contact["last_text"] == "oi" and contact["window_closes_at"] is not None
    cid = contact["id"]
    tagged = await api.patch(f"/api/v1/chat/contacts/{cid}", json={"tags": ["VIP", "vip", "lead"]})
    assert tagged.json()["contact"]["tags"] == ["vip", "lead"]
    replied = await api.post(f"/api/v1/chat/contacts/{cid}/messages", json={"text": "Oi, sou eu!"})
    assert replied.status_code == 202
    detail = replied.json()
    assert detail["contact"]["human_reason"] == "panel_reply"
    assert detail["scheduled"][0]["preview"] == "Oi, sou eu!"
    resumed = await api.patch(f"/api/v1/chat/contacts/{cid}", json={"human": False})
    assert resumed.json()["contact"]["human_until"] is None
    async with sf_of(chat_app)() as session, session.begin():
        await session.execute(
            update(Contact).values(last_inbound_at=utcnow() - timedelta(hours=30))
        )
    closed = await api.post(f"/api/v1/chat/contacts/{cid}/messages", json={"text": "oi"})
    assert closed.status_code == 409


@respx.mock
async def test_usernames_are_filled_even_without_an_answer(
    chat_app: FastAPI, keyed_settings: Settings, respx_mock: respx.MockRouter
) -> None:
    # Nothing matches and the AI is off: the bot sends nothing, but the @ still shows up.
    assert await receive(chat_app, dm("m1", "oi, tudo bem?")) == 0
    other = "17841499999999999"
    await receive(chat_app, dm("m2", "olá", sender=other))
    respx_mock.get(f"{GRAPH_HOST}/v25.0/{other}").mock(
        return_value=httpx.Response(400, json={"error": {"code": 100, "message": "nope"}})
    )
    async with httpx.AsyncClient() as http:
        w = worker(chat_app, keyed_settings, http)
        await w.run_until_idle()
        names = {c.ig_user_id: c.username for c in await all_rows(chat_app, Contact)}
        assert names == {FAN_ID: "fan", other: None}
        # A failed lookup is not repeated on every tick.
        calls = respx_mock.calls.call_count
        await w.run_until_idle()
        assert respx_mock.calls.call_count == calls
