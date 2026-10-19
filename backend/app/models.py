"""SQLAlchemy models. Source of truth for docs/data-model.md, together with the migrations."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db import UTCDateTime, utcnow


class Base(DeclarativeBase):
    pass


class WebhookStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    TOKEN_INVALID = "TOKEN_INVALID"  # noqa: S105 (status name, not a secret)


class MatchMode(StrEnum):
    WHOLE_WORD = "WHOLE_WORD"
    CONTAINS = "CONTAINS"


class Trigger(StrEnum):
    COMMENT = "COMMENT"
    MESSAGE = "MESSAGE"
    STORY_REPLY = "STORY_REPLY"


class Origin(StrEnum):
    WEBHOOK = "WEBHOOK"
    POLLING = "POLLING"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    UNCONFIRMED = "UNCONFIRMED"


class JobType(StrEnum):
    PRIVATE_REPLY = "PRIVATE_REPLY"
    # A DM to someone who messaged us first (story reply): addressed by user id, inside the
    # 24 h window, and outside the private-reply rate limit.
    DIRECT_MESSAGE = "DIRECT_MESSAGE"
    PUBLIC_REPLY = "PUBLIC_REPLY"
    # Follow gate: ask Meta whether the person follows (read only), then send the link.
    FOLLOW_CHECK = "FOLLOW_CHECK"
    FOLLOWUP_DM = "FOLLOWUP_DM"


# Jobs that deliver the campaign's DM. Their outcome lives in delivery.status.
DM_JOB_TYPES = (JobType.PRIVATE_REPLY, JobType.DIRECT_MESSAGE)


class GateState(StrEnum):
    """Where a follow-gate delivery stands. Empty (NULL) on deliveries without a gate."""

    WAITING = "WAITING"  # invite sent, nobody tapped yet
    CHECKING = "CHECKING"  # tapped; asking Meta whether the person follows
    PASSED = "PASSED"  # follows the account: the link goes out
    RELEASED = "RELEASED"  # Meta could not tell: the link goes out anyway
    REMINDED = "REMINDED"  # still not following after the checks: asked again
    STOPPED = "STOPPED"  # too many taps, nothing more is done


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    UNCONFIRMED = "UNCONFIRMED"


class WebhookEvent(Base):
    """Signed webhook bodies, stored before any processing."""

    __tablename__ = "webhook_event"
    __table_args__ = (Index("ix_webhook_event_status_received_at", "status", "received_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=WebhookStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OpsEvent(Base):
    """Operational log shown on the dashboard. Never holds secrets or user text."""

    __tablename__ = "ops_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(8))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Account(Base):
    """The single Instagram professional account of this instance (id is always 1)."""

    __tablename__ = "account"
    __table_args__ = (CheckConstraint("id = 1", name="ck_account_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ig_user_id: Mapped[str] = mapped_column(String(32))
    username: Mapped[str] = mapped_column(String(64))
    token_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    token_set_at: Mapped[datetime] = mapped_column(UTCDateTime())
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    token_expiry_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    token_refreshed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    token_refresh_attempted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(16), default=AccountStatus.ACTIVE)
    sending_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_subscribed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Campaign(Base):
    __tablename__ = "campaign"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    post_id: Mapped[str | None] = mapped_column(String(32), index=True)
    match_any_post: Mapped[bool] = mapped_column(Boolean, default=False)
    # What starts the campaign. Comments need a post target (post_id or match_any_post);
    # replies to stories match the keywords against any story.
    trigger_comments: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    trigger_story_replies: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    # A plain direct message (not a story reply) that contains a keyword.
    trigger_messages: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Follow gate: the first DM only invites the person to follow and holds a postback
    # button. The link (dm_message and link_url) goes out after Meta confirms the follow.
    follow_gate_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    follow_gate_message: Mapped[str | None] = mapped_column(Text)
    follow_gate_button_label: Mapped[str | None] = mapped_column(String(20))
    keywords: Mapped[list[str]] = mapped_column(JSON)
    match_mode: Mapped[str] = mapped_column(String(16), default=MatchMode.WHOLE_WORD)
    dm_message: Mapped[str] = mapped_column(Text)
    link_url: Mapped[str | None] = mapped_column(String(2048))
    link_button_label: Mapped[str | None] = mapped_column(String(20))
    public_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    public_reply_messages: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Delivery(Base):
    """One row per trigger. The unique constraints are the idempotency guarantee."""

    __tablename__ = "delivery"
    __table_args__ = (
        UniqueConstraint("campaign_id", "source_id", name="uq_delivery_campaign_source"),
        # Meta allows one private reply per comment, so one delivery per trigger overall.
        UniqueConstraint("trigger", "source_id", name="uq_delivery_trigger_source"),
        Index("ix_delivery_status", "status"),
        Index("ix_delivery_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id"))
    trigger: Mapped[str] = mapped_column(String(16), default=Trigger.COMMENT)
    origin: Mapped[str] = mapped_column(String(16), default=Origin.WEBHOOK)
    source_id: Mapped[str] = mapped_column(String(64))
    media_id: Mapped[str | None] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    actor_username: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)
    matched_keyword: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    dm_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    dm_message_id: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    public_reply_status: Mapped[str | None] = mapped_column(String(16))
    public_reply_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    public_reply_error: Mapped[str | None] = mapped_column(Text)
    # Follow gate. status above is the first DM (the invite); the link DM is tracked here.
    gate_state: Mapped[str | None] = mapped_column(String(16))
    gate_taps: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    gate_user_id: Mapped[str | None] = mapped_column(String(64))
    gate_tapped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    followup_status: Mapped[str | None] = mapped_column(String(16))
    followup_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    followup_message_id: Mapped[str | None] = mapped_column(String(128))
    followup_error: Mapped[str | None] = mapped_column(Text)
    anonymized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_status_run_at", "status", "run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    delivery_id: Mapped[int] = mapped_column(ForeignKey("delivery.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    send_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Set after Meta rejects the button template: the retry sends the link inside the text.
    fallback_text: Mapped[bool] = mapped_column(Boolean, default=False)
    # Small job-specific data. A follow check keeps its number (1 to 3) and the tapper's id.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class PostbackReceipt(Base):
    """One row per postback event we handled. Meta can deliver the same tap twice, and a
    restart must not turn a replay into a new tap."""

    __tablename__ = "postback_receipt"

    mid: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class LoginAttempt(Base):
    """Failed dashboard logins, per opaque client key. The dashboard runs on serverless
    instances that share no memory, so the throttle lives here."""

    __tablename__ = "login_attempt"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_failure_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    blocked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


class SendEvent(Base):
    """Rate-limit ledger: one row per private reply that may have left."""

    __tablename__ = "send_event"
    __table_args__ = (Index("ix_send_event_kind_sent_at", "kind", "sent_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    job_id: Mapped[int | None] = mapped_column(Integer)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ---- insights (read-only copy of what Meta reports about the account) ---------------------


class InsightMedia(Base):
    """One post or reel and its latest metrics. Refreshed by the insights sync."""

    __tablename__ = "insight_media"
    __table_args__ = (Index("ix_insight_media_posted_at", "posted_at"),)

    media_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    posted_at: Mapped[datetime] = mapped_column(UTCDateTime())
    media_type: Mapped[str] = mapped_column(String(24))
    product_type: Mapped[str | None] = mapped_column(String(16))
    caption: Mapped[str | None] = mapped_column(Text)
    permalink: Mapped[str | None] = mapped_column(String(2048))
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    media_url: Mapped[str | None] = mapped_column(String(2048))
    like_count: Mapped[int | None] = mapped_column(Integer)
    comments_count: Mapped[int | None] = mapped_column(Integer)
    # Metric name -> number, as Meta names them (reach, views, saved, shares, ...). A JSON map
    # because Meta adds and retires metrics often, and a missing metric is not a zero.
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Words used in the comments, counted without keeping the comments (term -> count).
    terms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    comments_seen_count: Mapped[int | None] = mapped_column(Integer)
    insights_fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    insights_error: Mapped[str | None] = mapped_column(Text)
    listed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class InsightDay(Base):
    """Account metrics of one local day (INSIGHTS_UTC_OFFSET_MINUTES)."""

    __tablename__ = "insight_day"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    followers_count: Mapped[int | None] = mapped_column(Integer)
    # Meta may still revise a day for 48 hours; after that it is not fetched again.
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class InsightSnapshot(Base):
    """The latest answer for things Meta only reports as a whole (demographics, profile)."""

    __tablename__ = "insight_snapshot"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[Any] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class InsightComment(Base):
    """When someone commented, for activity hours and recurring commenters. Never the text.
    Who commented is dropped after RETENTION_DELIVERY_DAYS, like in the deliveries."""

    __tablename__ = "insight_comment"
    __table_args__ = (Index("ix_insight_comment_commented_at", "commented_at"),)

    comment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    media_id: Mapped[str] = mapped_column(String(32), index=True)
    commented_at: Mapped[datetime] = mapped_column(UTCDateTime())
    author_id: Mapped[str | None] = mapped_column(String(64))
    username: Mapped[str | None] = mapped_column(String(64))


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Competitor(Base):
    """A public Instagram profile watched for the competitor analysis. One row (is_self) is the
    owner's own account, collected the same way so the comparison is like for like."""

    __tablename__ = "competitor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    is_self: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    full_name: Mapped[str | None] = mapped_column(String(256))
    biography: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(128))
    external_url: Mapped[str | None] = mapped_column(String(2048))
    profile_pic_url: Mapped[str | None] = mapped_column(String(2048))
    followers: Mapped[int | None] = mapped_column(Integer)
    follows: Mapped[int | None] = mapped_column(Integer)
    posts_count: Mapped[int | None] = mapped_column(Integer)
    verified: Mapped[bool | None] = mapped_column(Boolean)
    private: Mapped[bool | None] = mapped_column(Boolean)
    scraped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    scrape_error: Mapped[str | None] = mapped_column(Text)
    # Decision model answers for the profile questions: {question: {value, confidence}}.
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # TAXONOMY_VERSION the labels were made with. A new taxonomy reclassifies everything.
    labels_version: Mapped[str | None] = mapped_column(String(16))
    classify_error: Mapped[str | None] = mapped_column(Text)


class CompetitorPost(Base):
    """One of a watched profile's latest posts, as Apify reports it."""

    __tablename__ = "competitor_post"
    __table_args__ = (Index("ix_competitor_post_competitor_posted", "competitor_id", "posted_at"),)

    post_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitor.id", ondelete="CASCADE"))
    short_code: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(2048))
    media_type: Mapped[str | None] = mapped_column(String(16))
    product_type: Mapped[str | None] = mapped_column(String(16))
    caption: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    posted_at: Mapped[datetime] = mapped_column(UTCDateTime())
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    display_url: Mapped[str | None] = mapped_column(String(2048))
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    scraped_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    labels_version: Mapped[str | None] = mapped_column(String(16))
    classify_error: Mapped[str | None] = mapped_column(Text)


class CompetitorScan(Base):
    """One Apify run. At most one is RUNNING at a time."""

    __tablename__ = "competitor_scan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    apify_run_id: Mapped[str | None] = mapped_column(String(64))
    dataset_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.RUNNING)
    usernames: Mapped[list[str]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    profiles: Mapped[int] = mapped_column(Integer, default=0)
    posts: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)


class CompetitorReport(Base):
    """A comparative report written by the language model, on demand."""

    __tablename__ = "competitor_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.RUNNING)
    model: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    content: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)


class ChatDirection(StrEnum):
    IN = "IN"
    OUT = "OUT"


class ChatSource(StrEnum):
    """Who wrote a message. USER is the person; the rest are on the account's side."""

    USER = "USER"
    OWNER = "OWNER"  # typed by hand: in the Instagram app (seen through the echo) or the panel
    CAMPAIGN = "CAMPAIGN"
    FLOW = "FLOW"
    FOLLOWUP = "FOLLOWUP"
    AI = "AI"
    HANDOFF = "HANDOFF"


class OutboxKind(StrEnum):
    STEP = "STEP"  # a flow step: its text, link button or quick replies
    LINK = "LINK"  # the link of a tapped option
    TEXT = "TEXT"  # a fixed text: the handoff message, or the owner's reply from the panel
    FOLLOWUP = "FOLLOWUP"  # a step's follow-up, sent later inside the 24 h window
    AI_REPLY = "AI_REPLY"  # written by the language model when it runs


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SENT = "SENT"
    FAILED = "FAILED"
    UNCONFIRMED = "UNCONFIRMED"
    CANCELLED = "CANCELLED"


class Setting(Base):
    """Settings edited in the panel, as JSON per key (the chat config lives under 'chat')."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Contact(Base):
    """Someone who sent the account a direct message. Identified by the Instagram-scoped id."""

    __tablename__ = "contact"
    __table_args__ = (Index("ix_contact_last_inbound_at", "last_inbound_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ig_user_id: Mapped[str] = mapped_column(String(64), unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # The 24 h window Meta allows for replies opens at the person's last message.
    last_inbound_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_outbound_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # While set and in the future, nothing automatic answers this person.
    human_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    human_reason: Mapped[str | None] = mapped_column(String(32))
    messages_in: Mapped[int] = mapped_column(Integer, default=0)
    messages_out: Mapped[int] = mapped_column(Integer, default=0)
    anonymized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ChatMessage(Base):
    """One message of a conversation, both directions. The text is personal data: it is
    cleared after RETENTION_DELIVERY_DAYS."""

    __tablename__ = "chat_message"
    __table_args__ = (Index("ix_chat_message_contact_created", "contact_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String(4))
    source: Mapped[str] = mapped_column(String(16))
    # Meta's message id. Unique, so a webhook delivered twice records the message once.
    mid: Mapped[str | None] = mapped_column(String(256), unique=True)
    text: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[str | None] = mapped_column(String(256))
    flow_id: Mapped[int | None] = mapped_column(Integer)
    step_id: Mapped[str | None] = mapped_column(String(32))
    cost_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class Flow(Base):
    """A chat flow: steps with text, links and quick-reply options, started by a keyword."""

    __tablename__ = "flow"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    match_mode: Mapped[str] = mapped_column(String(16), default=MatchMode.WHOLE_WORD)
    # Validated by app.flows.FlowInput. The first step is where the flow starts.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ChatOutbox(Base):
    """Chat messages waiting to go out. Same safety rules as the job queue: send_started_at is
    written before calling Meta, and an ambiguous result is UNCONFIRMED, never resent."""

    __tablename__ = "chat_outbox"
    __table_args__ = (
        Index("ix_chat_outbox_status_run_at", "status", "run_at"),
        Index("ix_chat_outbox_contact", "contact_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=OutboxStatus.PENDING)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    send_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    message_id: Mapped[str | None] = mapped_column(String(256))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
