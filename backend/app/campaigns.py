"""Campaign input validation and persistence. Shared by the CLI and the admin API."""

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import utcnow
from app.gate import DEFAULT_BUTTON_LABEL
from app.matching import normalize_keywords
from app.meta.limits import BUTTON_TITLE_MAX_CHARS, MESSAGE_TEXT_MAX_CHARS
from app.models import Campaign, MatchMode

NAME_MAX_CHARS = 80
MAX_KEYWORDS = 20
KEYWORD_MAX_CHARS = 100
LINK_MAX_CHARS = 2048
MAX_PUBLIC_REPLIES = 10
PUBLIC_REPLY_MAX_CHARS = 300
# The invite is sent as a button template, which Meta caps below a plain text message.
GATE_MESSAGE_MAX_CHARS = 640


def validate_link_url(url: str) -> str:
    """https only, a real host, no credentials. The backend never fetches it (no SSRF)."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise ValueError("link must start with https://")
    if not parts.hostname or "." not in parts.hostname:
        raise ValueError("link needs a valid host")
    if parts.username or parts.password:
        raise ValueError("link must not embed credentials")
    if any(ch.isspace() for ch in url.strip()):
        raise ValueError("link must not contain spaces")
    return url.strip()


class CampaignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX_CHARS)
    keywords: list[str] = Field(min_length=1, max_length=MAX_KEYWORDS)
    match_mode: MatchMode = MatchMode.WHOLE_WORD
    post_id: str | None = Field(default=None, pattern=r"^\d{5,32}$")
    match_any_post: bool = False
    trigger_comments: bool = True
    trigger_story_replies: bool = False
    trigger_messages: bool = False
    dm_message: str = Field(min_length=1, max_length=MESSAGE_TEXT_MAX_CHARS)
    link_url: str | None = Field(default=None, max_length=LINK_MAX_CHARS)
    link_button_label: str | None = Field(
        default=None, min_length=1, max_length=BUTTON_TITLE_MAX_CHARS
    )
    follow_gate_enabled: bool = False
    follow_gate_message: str | None = Field(default=None, max_length=GATE_MESSAGE_MAX_CHARS)
    follow_gate_button_label: str | None = Field(
        default=None, min_length=1, max_length=BUTTON_TITLE_MAX_CHARS
    )
    public_reply_messages: list[str] = Field(default_factory=list, max_length=MAX_PUBLIC_REPLIES)
    is_active: bool = True

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        if any(len(k) > KEYWORD_MAX_CHARS for k in value):
            raise ValueError(f"each keyword must have at most {KEYWORD_MAX_CHARS} characters")
        if not normalize_keywords(value):
            raise ValueError("keywords must contain letters or digits")
        return [k.strip() for k in value if k.strip()]

    @field_validator("link_url")
    @classmethod
    def _link(cls, value: str | None) -> str | None:
        return validate_link_url(value) if value else None

    @field_validator("public_reply_messages")
    @classmethod
    def _public(cls, value: list[str]) -> list[str]:
        cleaned = [m.strip() for m in value if m.strip()]
        if any(len(m) > PUBLIC_REPLY_MAX_CHARS for m in cleaned):
            raise ValueError(
                f"public replies must have at most {PUBLIC_REPLY_MAX_CHARS} characters"
            )
        return cleaned

    @model_validator(mode="after")
    def _rules(self) -> "CampaignInput":
        if not (self.trigger_comments or self.trigger_story_replies or self.trigger_messages):
            raise ValueError("choose at least one trigger: comments, story replies or messages")
        if self.trigger_comments:
            if bool(self.post_id) == self.match_any_post:
                raise ValueError("choose exactly one: a post_id or match_any_post")
        else:
            if self.post_id or self.match_any_post:
                raise ValueError("a post target only applies to campaigns that answer comments")
            if self.public_reply_messages:
                raise ValueError("public replies only apply to campaigns that answer comments")
        if self.link_button_label and not self.link_url:
            raise ValueError("a button label needs a link_url")
        if self.follow_gate_enabled:
            if not self.link_url:
                raise ValueError("the follow gate holds back a link: add a link_url")
            if not (self.follow_gate_message or "").strip():
                raise ValueError("the follow gate needs an invite message")
            self.follow_gate_message = (self.follow_gate_message or "").strip()
            self.follow_gate_button_label = (
                self.follow_gate_button_label or DEFAULT_BUTTON_LABEL
            ).strip()
        size = len(self.dm_message) + (len(self.link_url) + 1 if self.link_url else 0)
        if size > MESSAGE_TEXT_MAX_CHARS:
            raise ValueError(f"message plus link exceeds {MESSAGE_TEXT_MAX_CHARS} characters")
        return self


COPY_SUFFIX = " (copy)"


def _apply(campaign: Campaign, data: CampaignInput) -> None:
    campaign.name = data.name
    campaign.is_active = data.is_active
    campaign.post_id = data.post_id
    campaign.match_any_post = data.match_any_post
    campaign.trigger_comments = data.trigger_comments
    campaign.trigger_story_replies = data.trigger_story_replies
    campaign.trigger_messages = data.trigger_messages
    campaign.keywords = data.keywords
    campaign.match_mode = data.match_mode
    campaign.dm_message = data.dm_message
    campaign.link_url = data.link_url
    campaign.link_button_label = data.link_button_label
    # The invite only exists while the gate is on, so switching it off clears a stale text.
    campaign.follow_gate_enabled = data.follow_gate_enabled
    campaign.follow_gate_message = data.follow_gate_message if data.follow_gate_enabled else None
    campaign.follow_gate_button_label = (
        data.follow_gate_button_label if data.follow_gate_enabled else None
    )
    campaign.public_reply_enabled = bool(data.public_reply_messages)
    campaign.public_reply_messages = data.public_reply_messages


def as_input(campaign: Campaign) -> dict[str, Any]:
    """The stored campaign in CampaignInput's shape, to merge a partial edit into."""
    return {
        "name": campaign.name,
        "keywords": list(campaign.keywords),
        "match_mode": campaign.match_mode,
        "post_id": campaign.post_id,
        "match_any_post": campaign.match_any_post,
        "trigger_comments": campaign.trigger_comments,
        "trigger_story_replies": campaign.trigger_story_replies,
        "trigger_messages": campaign.trigger_messages,
        "dm_message": campaign.dm_message,
        "link_url": campaign.link_url,
        "link_button_label": campaign.link_button_label,
        "follow_gate_enabled": campaign.follow_gate_enabled,
        "follow_gate_message": campaign.follow_gate_message,
        "follow_gate_button_label": campaign.follow_gate_button_label,
        "public_reply_messages": list(campaign.public_reply_messages),
        "is_active": campaign.is_active,
    }


async def create_campaign(
    session_factory: async_sessionmaker[AsyncSession], data: CampaignInput
) -> Campaign:
    async with session_factory() as session, session.begin():
        campaign = Campaign()
        _apply(campaign, data)
        session.add(campaign)
    return campaign


async def update_campaign(
    session_factory: async_sessionmaker[AsyncSession], campaign_id: int, changes: dict[str, Any]
) -> Campaign | None:
    """Merge `changes` into the stored campaign and validate the whole result.

    Raises pydantic.ValidationError when the merged campaign is invalid.
    """
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None or campaign.deleted_at is not None:
            return None
        _apply(campaign, CampaignInput.model_validate({**as_input(campaign), **changes}))
        return campaign


async def duplicate_campaign(
    session_factory: async_sessionmaker[AsyncSession], campaign_id: int
) -> Campaign | None:
    """Copy as inactive, so two campaigns never answer the same post by accident."""
    async with session_factory() as session, session.begin():
        source = await session.get(Campaign, campaign_id)
        if source is None or source.deleted_at is not None:
            return None
        data = as_input(source)
        data["name"] = source.name[: NAME_MAX_CHARS - len(COPY_SUFFIX)] + COPY_SUFFIX
        data["is_active"] = False
        copy = Campaign()
        _apply(copy, CampaignInput.model_validate(data))
        session.add(copy)
    return copy


async def list_campaigns(session: AsyncSession) -> list[Campaign]:
    result = await session.execute(
        select(Campaign).where(Campaign.deleted_at.is_(None)).order_by(Campaign.id)
    )
    return list(result.scalars())


async def set_campaign_state(
    session_factory: async_sessionmaker[AsyncSession],
    campaign_id: int,
    *,
    active: bool | None = None,
    delete: bool = False,
    now: datetime | None = None,
) -> Campaign | None:
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        if campaign is None or campaign.deleted_at is not None:
            return None
        if active is not None:
            campaign.is_active = active
        if delete:
            # Soft delete: deliveries keep pointing at it for history and totals.
            campaign.is_active = False
            campaign.deleted_at = now or utcnow()
        return campaign
