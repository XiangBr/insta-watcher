"""Request and response bodies of the admin API. The dashboard's types are generated from these."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.flows import ChatConfig, FlowStep
from app.models import MatchMode

# ---- status and account ------------------------------------------------------------------


class StatusAccount(BaseModel):
    username: str
    ig_user_id: str
    status: str
    sending_paused: bool
    token_days_left: int | None
    token_expiry_confirmed: bool


class StatusJobs(BaseModel):
    pending: int
    running: int
    unconfirmed: int
    failed: int


class StatusRateLimit(BaseModel):
    used: int
    limit: int


class StatusOut(BaseModel):
    account: StatusAccount | None
    active_campaigns: int
    last_webhook_at: datetime | None
    pending_webhook_events: int
    jobs: StatusJobs
    rate_limit: StatusRateLimit
    unresolved_errors: int


class AccountOut(BaseModel):
    """Never carries the token, encrypted or not."""

    username: str
    ig_user_id: str
    status: str
    sending_paused: bool
    token_set_at: datetime
    token_expires_at: datetime | None
    token_expiry_confirmed: bool
    token_refreshed_at: datetime | None
    webhook_subscribed_at: datetime | None


class TokenIn(BaseModel):
    token: SecretStr = Field(min_length=10, max_length=2048)


class PostOut(BaseModel):
    id: str
    timestamp: str | None = None
    permalink: str | None = None
    caption: str | None = None
    media_type: str | None = None
    media_url: str | None = None
    thumbnail_url: str | None = None


# ---- campaigns ---------------------------------------------------------------------------


class DeliveryCounts(BaseModel):
    pending: int = 0
    sent: int = 0
    failed: int = 0
    unconfirmed: int = 0


class CampaignOut(BaseModel):
    id: int
    name: str
    is_active: bool
    post_id: str | None
    match_any_post: bool
    trigger_comments: bool
    trigger_story_replies: bool
    trigger_messages: bool
    keywords: list[str]
    match_mode: MatchMode
    dm_message: str
    link_url: str | None
    link_button_label: str | None
    follow_gate_enabled: bool
    follow_gate_message: str | None
    follow_gate_button_label: str | None
    public_reply_messages: list[str]
    created_at: datetime
    updated_at: datetime
    deliveries: DeliveryCounts


class CampaignPatch(BaseModel):
    """Only the fields sent are changed. The merged campaign is validated as a whole."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    keywords: list[str] | None = None
    match_mode: MatchMode | None = None
    post_id: str | None = None
    match_any_post: bool | None = None
    trigger_comments: bool | None = None
    trigger_story_replies: bool | None = None
    trigger_messages: bool | None = None
    dm_message: str | None = None
    link_url: str | None = None
    link_button_label: str | None = None
    follow_gate_enabled: bool | None = None
    follow_gate_message: str | None = None
    follow_gate_button_label: str | None = None
    public_reply_messages: list[str] | None = None
    is_active: bool | None = None


class DayStats(BaseModel):
    day: str
    sent: int = 0
    failed: int = 0
    unconfirmed: int = 0
    pending: int = 0


class CampaignStats(BaseModel):
    campaign_id: int
    days: list[DayStats]
    totals: DeliveryCounts


class LimitsOut(BaseModel):
    """Validation limits the campaign form shows, so the dashboard keeps no copy of them."""

    name_max: int
    keywords_max: int
    keyword_max: int
    message_max: int
    button_title_max: int
    link_max: int
    public_replies_max: int
    public_reply_max: int
    gate_message_max: int


class MatchTestIn(BaseModel):
    keywords: list[str] = Field(max_length=50)
    match_mode: MatchMode = MatchMode.WHOLE_WORD
    text: str = Field(max_length=2200)


class MatchTestOut(BaseModel):
    matched: bool
    keyword: str | None


# ---- deliveries and operation ------------------------------------------------------------


class DeliveryOut(BaseModel):
    id: int
    created_at: datetime
    campaign_id: int
    campaign_name: str
    trigger: str
    origin: str
    media_id: str | None
    actor_username: str | None
    text: str | None
    matched_keyword: str | None
    status: str
    attempts: int
    dm_sent_at: datetime | None
    error_message: str | None
    public_reply_status: str | None
    public_reply_sent_at: datetime | None
    public_reply_error: str | None
    gate_state: str | None
    gate_taps: int
    followup_status: str | None
    followup_sent_at: datetime | None
    followup_error: str | None
    anonymized: bool


class DeliveryPage(BaseModel):
    items: list[DeliveryOut]
    next_cursor: int | None = Field(description="pass as `before` to get the next page")


class RetryIn(BaseModel):
    confirm_not_delivered: bool = False


class OpsEventOut(BaseModel):
    id: int
    created_at: datetime
    source: str
    level: str
    message: str
    payload: dict[str, Any] | None
    resolved_at: datetime | None


class WebhookEventOut(BaseModel):
    id: int
    received_at: datetime
    status: str
    error: str | None
    changes: list[dict[str, Any]]


class ReconcileOut(BaseModel):
    queued: int


# ---- dashboard login throttle ------------------------------------------------------------


class ThrottleKeyIn(BaseModel):
    key: str = Field(
        pattern=r"^[A-Za-z0-9_-]{16,128}$",
        description="HMAC of the client IP computed by the dashboard; never the IP itself",
    )


class ThrottleOut(BaseModel):
    retry_after: int = Field(description="seconds to wait before the next attempt; 0 = allowed")


# ---- insights ----------------------------------------------------------------------------


class InsightsSyncOut(BaseModel):
    finished_at: datetime | None
    running: bool
    partial: bool
    missing_permission: bool
    errors: list[str]
    interval_seconds: int


class InsightsProfile(BaseModel):
    username: str | None = None
    name: str | None = None
    biography: str | None = None
    website: str | None = None
    followers_count: int | None = None
    follows_count: int | None = None
    media_count: int | None = None
    profile_picture_url: str | None = None


class InsightPost(BaseModel):
    id: str
    posted_at: datetime
    media_type: str
    kind: str = Field(description="REELS or FEED")
    caption: str | None
    permalink: str | None
    thumbnail_url: str | None
    media_url: str | None
    like_count: int | None
    comments_count: int | None
    metrics: dict[str, float] = Field(description="Meta's names: reach, views, saved, shares...")
    terms: dict[str, int] = Field(description="words used in the comments, normalized")
    insights_error: str | None


class InsightDayOut(BaseModel):
    day: str
    followers_count: int | None
    complete: bool
    metrics: dict[str, float]


class Demographics(BaseModel):
    age: dict[str, float] | None = None
    gender: dict[str, float] | None = None
    city: dict[str, float] | None = None
    country: dict[str, float] | None = None


class InsightsAudience(BaseModel):
    fetched_at: datetime
    window_days: int
    followers: Demographics | None = None
    engaged: Demographics | None = None
    reach_by_follow_type: dict[str, float] | None = None
    reach_by_product: dict[str, float] | None = None
    interactions_by_product: dict[str, float] | None = None
    online_followers: dict[str, float] | None = Field(
        default=None, description="average followers online per local hour (0-23)"
    )


class InsightsOut(BaseModel):
    sync: InsightsSyncOut
    utc_offset_minutes: int
    profile: InsightsProfile | None
    posts: list[InsightPost]
    days: list[InsightDayOut]
    audience: InsightsAudience | None


class ActivityHour(BaseModel):
    hour: datetime = Field(description="start of the UTC hour")
    count: int


class Commenter(BaseModel):
    username: str
    comments: int
    posts: int
    last_at: datetime


class KeywordCount(BaseModel):
    keyword: str
    count: int


class InsightsActivityOut(BaseModel):
    hours: list[ActivityHour]
    total_comments: int
    unique_commenters: int
    returning_commenters: int = Field(description="commented on two or more posts")
    top_commenters: list[Commenter]
    keywords: list[KeywordCount] = Field(description="campaign keywords people asked for")


class InsightsSyncStarted(BaseModel):
    started: bool


# ---- competitors -------------------------------------------------------------------------


class CompetitorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=1, max_length=200, description="@user, user or the profile URL"
    )


class Label(BaseModel):
    value: str | float = Field(description="option key (choice), level index (score), P(true)")
    confidence: float | None = None


class CompetitorPostOut(BaseModel):
    id: str
    url: str | None
    media_type: str | None
    caption: str | None = Field(description="cut to 500 characters")
    posted_at: datetime
    likes: int | None
    comments: int | None
    views: int | None
    display_url: str | None
    is_pinned: bool
    engagement_rate: float | None = Field(description="(likes + comments) / followers")
    performance: float | None = Field(
        description="engagement rate over the profile's median: 1.0 is typical for that profile"
    )
    labels: dict[str, Label]


class CompetitorStats(BaseModel):
    posts: int
    median_engagement_rate: float | None
    avg_likes: float | None
    avg_comments: float | None
    posts_per_week: float | None
    video_share: float | None = Field(description="share of videos and reels among the posts")


class CompetitorOut(BaseModel):
    id: int
    username: str
    is_self: bool
    full_name: str | None
    biography: str | None
    category: str | None
    external_url: str | None
    profile_pic_url: str | None
    followers: int | None
    follows: int | None
    posts_count: int | None
    verified: bool | None
    private: bool | None
    scraped_at: datetime | None
    scrape_error: str | None
    classify_error: str | None
    labels: dict[str, Label]
    stats: CompetitorStats
    posts: list[CompetitorPostOut]


class OptionStat(BaseModel):
    option: str
    posts: int
    median_performance: float


class TaxonomyOption(BaseModel):
    key: str
    label: str


class TaxonomyQuestion(BaseModel):
    key: str
    type: str = Field(description="choice, score or noul")
    label: str
    options: list[TaxonomyOption]


class Taxonomy(BaseModel):
    version: str
    post: list[TaxonomyQuestion]
    profile: list[TaxonomyQuestion]


class CompetitorScanOut(BaseModel):
    status: str
    started_at: datetime
    finished_at: datetime | None
    profiles: int
    posts: int
    cost_usd: float | None
    error: str | None


class CompetitorsOut(BaseModel):
    collect_enabled: bool = Field(description="APIFY_TOKEN is set")
    classify_enabled: bool = Field(description="OPENROUTER_API_KEY is set")
    running: bool
    refresh_hours: int
    max_competitors: int
    last_scan: CompetitorScanOut | None
    pending_classification: int
    classify_cost_usd: float = Field(description="spent on the decision model so far")
    taxonomy: Taxonomy
    competitors: list[CompetitorOut]
    niche: dict[str, list[OptionStat]] = Field(
        description="per post question: how each option performs across competitors (not you)"
    )


class CompetitorsStarted(BaseModel):
    started: bool


class ReportCompetitor(BaseModel):
    username: str
    summary: str = ""
    strength: str = ""
    technologies: list[str] = Field(default_factory=list)


class ReportIdea(BaseModel):
    title: str
    format: str = ""
    hook: str = ""
    keyword: str = Field(default="", description="comment keyword for a insta-watcher campaign")
    why: str = ""


class ReportContent(BaseModel):
    summary: str = ""
    what_works: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    competitors: list[ReportCompetitor] = Field(default_factory=list)
    ideas: list[ReportIdea] = Field(default_factory=list)


class CompetitorReportOut(BaseModel):
    id: int
    status: str
    model: str
    created_at: datetime
    finished_at: datetime | None
    content: ReportContent | None
    cost_usd: float | None
    error: str | None


class CompetitorReportState(BaseModel):
    enabled: bool
    running: bool
    report: CompetitorReportOut | None


# ---- chat --------------------------------------------------------------------------------


class ChatSummary(BaseModel):
    contacts: int
    open_windows: int = Field(description="people who wrote in the last 24 h")
    in_human_mode: int
    ai_replies_24h: int
    ai_cost_24h_usd: float
    scheduled: int = Field(description="outbox messages waiting to go out")
    failed_24h: int


class ContactOut(BaseModel):
    id: int
    username: str | None
    tags: list[str]
    first_seen_at: datetime
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    window_closes_at: datetime | None = Field(description="end of the 24 h window, if open")
    human_until: datetime | None
    human_reason: str | None
    messages_in: int
    messages_out: int
    last_text: str | None = Field(description="the last message, cut to 120 characters")
    last_source: str | None


class ContactsPage(BaseModel):
    summary: ChatSummary
    contacts: list[ContactOut]
    total: int
    tags: list[str] = Field(description="every tag in use, for the filter")


class ChatMessageOut(BaseModel):
    id: int
    direction: str
    source: str
    text: str | None
    payload: str | None
    flow_id: int | None
    step_id: str | None
    cost_usd: float | None
    created_at: datetime


class OutboxOut(BaseModel):
    id: int
    kind: str
    status: str
    run_at: datetime
    preview: str | None
    error: str | None
    created_at: datetime


class ContactDetail(BaseModel):
    contact: ContactOut
    messages: list[ChatMessageOut]
    scheduled: list[OutboxOut]
    problems: list[OutboxOut] = Field(description="failed or unconfirmed sends, newest first")


class ContactPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tags: list[str] | None = None
    human: bool | None = Field(
        default=None, description="true pauses the automation (handoff_hours), false resumes"
    )


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)


class FlowOut(BaseModel):
    id: int
    name: str
    is_active: bool
    keywords: list[str]
    match_mode: MatchMode
    steps: list[FlowStep]
    created_at: datetime
    updated_at: datetime
    sent: int = Field(description="messages this flow sent")
    people: int = Field(description="people who received at least one of its messages")


class ChatUsage(BaseModel):
    ai_replies_24h: int
    ai_cost_24h_usd: float
    handoffs_24h: int


class ChatConfigOut(BaseModel):
    config: ChatConfig
    ai_available: bool = Field(description="OPENROUTER_API_KEY is set")
    default_model: str
    usage: ChatUsage


class AiTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1000)
    config: ChatConfig | None = Field(
        default=None, description="unsaved settings to try; the saved ones if empty"
    )


class AiTestOut(BaseModel):
    reply: str
    handoff: bool
    sent_text: str = Field(description="what would go out: the reply, or the handoff message")
    model: str
    cost_usd: float | None
