"""Chat flows and the chat settings: validation and storage. Shared by the API and the tests.

A flow is a list of steps. The first one is where it starts. Each step sends a text, with
either one link button or up to MAX_OPTIONS quick replies. Each option does one thing: go to
another step, send a link, or hand the conversation to a human.
"""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.campaigns import KEYWORD_MAX_CHARS, MAX_KEYWORDS, NAME_MAX_CHARS, validate_link_url
from app.db import utcnow
from app.matching import normalize_keywords
from app.meta.limits import (
    BUTTON_TITLE_MAX_CHARS,
    MESSAGE_TEXT_MAX_CHARS,
    MESSAGING_WINDOW,
    QUICK_REPLY_MAX,
    QUICK_REPLY_TITLE_MAX_CHARS,
)
from app.models import Flow, MatchMode, Setting

MAX_STEPS = 30
# Below Meta's 13: more than this does not fit on a phone screen without scrolling.
MAX_OPTIONS = min(10, QUICK_REPLY_MAX)
MAX_TAGS = 10
TAG_MAX_CHARS = 32
STEP_ID = r"^[a-z0-9_-]{1,32}$"
# A follow-up must leave room inside the 24 h window the person's last message opened.
FOLLOWUP_MAX_MINUTES = int(MESSAGING_WINDOW.total_seconds() // 60) - 60
DEFAULT_LINK_TEXT = "Aqui está:"
DEFAULT_LINK_LABEL = "Abrir"

_PAYLOAD = re.compile(r"^bwf:(\d{1,9}):([a-z0-9_-]{1,32}):(\d{1,2})$")


def option_payload(flow_id: int, step_id: str, index: int) -> str:
    return f"bwf:{flow_id}:{step_id}:{index}"


def parse_option_payload(payload: object) -> tuple[int, str, int] | None:
    """A quick-reply payload we sent. Anything else (other bots, the follow gate) is None."""
    if not isinstance(payload, str):
        return None
    match = _PAYLOAD.match(payload)
    if match is None:
        return None
    return int(match.group(1)), match.group(2), int(match.group(3))


def clean_tags(value: list[str]) -> list[str]:
    out: list[str] = []
    for tag in value:
        tag = tag.strip().lower()
        if tag and tag not in out:
            if len(tag) > TAG_MAX_CHARS:
                raise ValueError(f"tags have at most {TAG_MAX_CHARS} characters")
            out.append(tag)
    if len(out) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")
    return out


class FlowOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=QUICK_REPLY_TITLE_MAX_CHARS)
    action: Literal["step", "link", "handoff"]
    step: str | None = Field(default=None, pattern=STEP_ID)
    url: str | None = Field(default=None, max_length=2048)
    link_text: str | None = Field(default=None, max_length=MESSAGE_TEXT_MAX_CHARS)
    link_label: str | None = Field(default=None, min_length=1, max_length=BUTTON_TITLE_MAX_CHARS)
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return clean_tags(value)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return validate_link_url(value) if value else None

    @model_validator(mode="after")
    def _rules(self) -> "FlowOption":
        self.label = self.label.strip()
        if self.action == "step" and not self.step:
            raise ValueError("an option that goes to a step needs the step")
        if self.action == "link" and not self.url:
            raise ValueError("an option that sends a link needs the url")
        return self


class FlowFollowup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_minutes: int = Field(ge=1, le=FOLLOWUP_MAX_MINUTES)
    message: str = Field(min_length=1, max_length=MESSAGE_TEXT_MAX_CHARS)
    # Skip the follow-up if the person wrote anything after this step.
    only_if_no_reply: bool = True


class FlowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=STEP_ID)
    message: str = Field(min_length=1, max_length=MESSAGE_TEXT_MAX_CHARS)
    link_url: str | None = Field(default=None, max_length=2048)
    link_label: str | None = Field(default=None, min_length=1, max_length=BUTTON_TITLE_MAX_CHARS)
    options: list[FlowOption] = Field(default_factory=list, max_length=MAX_OPTIONS)
    tags: list[str] = Field(default_factory=list)
    followup: FlowFollowup | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return clean_tags(value)

    @field_validator("link_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return validate_link_url(value) if value else None

    @model_validator(mode="after")
    def _rules(self) -> "FlowStep":
        if self.link_url and self.options:
            # A button template cannot carry quick replies: one or the other per step.
            raise ValueError(f"step {self.id}: use a link button or options, not both")
        if self.link_label and not self.link_url:
            raise ValueError(f"step {self.id}: a button label needs a link_url")
        return self


class FlowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX_CHARS)
    is_active: bool = True
    keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS)
    match_mode: MatchMode = MatchMode.WHOLE_WORD
    steps: list[FlowStep] = Field(min_length=1, max_length=MAX_STEPS)

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        cleaned = [k.strip() for k in value if k.strip()]
        if any(len(k) > KEYWORD_MAX_CHARS for k in cleaned):
            raise ValueError(f"each keyword must have at most {KEYWORD_MAX_CHARS} characters")
        if cleaned and not normalize_keywords(cleaned):
            raise ValueError("keywords must contain letters or digits")
        return cleaned

    @model_validator(mode="after")
    def _rules(self) -> "FlowInput":
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("step ids must be unique")
        for step in self.steps:
            for option in step.options:
                if option.action == "step" and option.step not in ids:
                    raise ValueError(f"step {step.id}: option goes to unknown step {option.step}")
        return self


def apply_flow(flow: Flow, data: FlowInput) -> None:
    flow.name = data.name.strip()
    flow.is_active = data.is_active
    flow.keywords = data.keywords
    flow.match_mode = data.match_mode
    flow.steps = [s.model_dump() for s in data.steps]


def find_step(flow: Flow, step_id: str) -> FlowStep | None:
    for raw in flow.steps:
        if raw.get("id") == step_id:
            return FlowStep.model_validate(raw)
    return None


def first_step(flow: Flow) -> FlowStep | None:
    return FlowStep.model_validate(flow.steps[0]) if flow.steps else None


async def active_flows(session: AsyncSession) -> list[Flow]:
    result = await session.scalars(
        select(Flow).where(Flow.deleted_at.is_(None), Flow.is_active.is_(True)).order_by(Flow.id)
    )
    return list(result)


async def soft_delete_flow(session: AsyncSession, flow: Flow, now: datetime | None = None) -> None:
    flow.is_active = False
    flow.deleted_at = now or utcnow()


# ---- chat settings -----------------------------------------------------------------------

CHAT_SETTINGS_KEY = "chat"
PERSONA_MAX_CHARS = 2000
KNOWLEDGE_MAX_CHARS = 20000


class ChatConfig(BaseModel):
    """Edited in the panel. Everything automatic starts off."""

    model_config = ConfigDict(extra="forbid")

    ai_enabled: bool = False
    # Empty: CHAT_AI_MODEL from the environment.
    ai_model: str | None = Field(default=None, max_length=128)
    persona: str = Field(
        default=(
            "Você é o assistente da conta no Instagram. Responde de forma simpática, direta "
            "e curta, como numa conversa de direct."
        ),
        max_length=PERSONA_MAX_CHARS,
    )
    knowledge: str = Field(default="", max_length=KNOWLEDGE_MAX_CHARS)
    handoff_message: str = Field(
        default="Vou passar sua mensagem pra equipe, já já alguém te responde por aqui.",
        min_length=1,
        max_length=MESSAGE_TEXT_MAX_CHARS,
    )
    handoff_hours: int = Field(default=12, ge=1, le=168)
    ai_max_per_contact_day: int = Field(default=10, ge=1, le=100)
    ai_max_per_hour: int = Field(default=60, ge=1, le=1000)
    # Answer the first message someone ever sends with this flow (when nothing else matched).
    welcome_flow_id: int | None = None
    # A reply typed by hand in the Instagram app pauses the automation for that person.
    pause_on_manual_reply: bool = True

    @field_validator("ai_model")
    @classmethod
    def _model(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not re.match(r"^[\w.~:/-]+$", value):
            raise ValueError("model id has unexpected characters")
        return value


async def load_chat_config(session: AsyncSession) -> ChatConfig:
    row = await session.get(Setting, CHAT_SETTINGS_KEY)
    if row is None or not isinstance(row.value, dict):
        return ChatConfig()
    try:
        return ChatConfig.model_validate(row.value)
    except ValueError:
        # A stored config that no longer validates (an older version): start from defaults,
        # never crash the dispatcher over it.
        return ChatConfig()


async def save_chat_config(session: AsyncSession, config: ChatConfig) -> None:
    row = await session.get(Setting, CHAT_SETTINGS_KEY)
    value: dict[str, Any] = config.model_dump()
    if row is None:
        session.add(Setting(key=CHAT_SETTINGS_KEY, value=value))
    else:
        row.value = value
