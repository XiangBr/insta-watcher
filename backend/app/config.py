"""Settings loaded from the environment. Fails closed on missing or weak secrets."""

import re
from functools import lru_cache
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.meta.limits import PRIVATE_REPLY_HOURLY_LIMIT

# Values that show up in docs, tutorials and .env.example files.
_PLACEHOLDER_MARKERS = (
    "change",
    "example",
    "placeholder",
    "secret",
    "your",
    "seu",
    "sua",
    "todo",
    "xxx",
    "test",
)
_HEX_64 = re.compile(r"^[0-9a-fA-F]{64}$")


def _strong(min_len: int) -> AfterValidator:
    def check(value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < min_len:
            raise ValueError(f"must be at least {min_len} characters")
        lowered = raw.lower()
        if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
            raise ValueError("looks like a placeholder value")
        if len(set(raw)) < 8:
            raise ValueError("too little variety in characters")
        return value

    return AfterValidator(check)


def _hex_key(value: SecretStr) -> SecretStr:
    if not _HEX_64.match(value.get_secret_value()):
        raise ValueError("must be 64 hex characters (openssl rand -hex 32)")
    return value


# Meta app secrets are 32 hex characters.
AppSecret = Annotated[SecretStr, _strong(32)]
GeneratedSecret = Annotated[SecretStr, _strong(32)]
LongSecret = Annotated[SecretStr, _strong(64)]
EncryptionKey = Annotated[SecretStr, AfterValidator(_hex_key), _strong(64)]


def _empty_is_unset(value: object) -> object:
    # An empty variable (KEY= copied from .env.example) means "not configured".
    return None if isinstance(value, str) and not value.strip() else value


def _api_key(value: SecretStr | None) -> SecretStr | None:
    # Keys issued by a third party (Apify, OpenRouter): random enough by construction, so only
    # the obvious mistakes are refused. The placeholder check would reject real keys by chance.
    if value is None:
        return None
    raw = value.get_secret_value()
    if len(raw) < 20 or raw != raw.strip() or len(set(raw)) < 8:
        raise ValueError("does not look like an API key")
    return value


PublicUrl = Annotated[str, Field(pattern=r"^https://[^\s/?#]+$", max_length=255)]
ContactEmail = Annotated[
    str, Field(max_length=254, pattern=r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")
]


ApiKey = Annotated[SecretStr | None, BeforeValidator(_empty_is_unset), AfterValidator(_api_key)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    # Secrets: no defaults on purpose. The app refuses to start without them.
    meta_app_secret: AppSecret
    instagram_app_secret: AppSecret
    webhook_verify_token: GeneratedSecret
    encryption_key: EncryptionKey
    api_admin_token: LongSecret
    click_hash_salt: GeneratedSecret

    database_url: str = Field(min_length=1)
    meta_graph_api_version: str = Field(default="v25.0", pattern=r"^v\d+\.\d+$")
    enable_api_docs: bool = False
    webhook_max_body_bytes: int = Field(default=256 * 1024, gt=0)

    # Background loops. Tests turn them off and call the steps directly.
    background_tasks_enabled: bool = True
    maintenance_interval_seconds: int = Field(default=3600, ge=60)

    # Retention, in days. The privacy policy page states these defaults.
    retention_webhook_days: int = Field(default=7, gt=0)
    retention_delivery_days: int = Field(default=90, gt=0)
    retention_ops_days: int = Field(default=90, gt=0)

    token_refresh_when_days: int = Field(default=20, gt=0, lt=60)

    # Sending. The hourly limit can be lowered (if Meta throttles earlier), never raised.
    private_reply_hourly_limit: int = Field(default=PRIVATE_REPLY_HOURLY_LIMIT, gt=0, le=750)
    worker_concurrency: int = Field(default=3, ge=1, le=10)
    worker_interval_seconds: float = Field(default=2.0, gt=0)
    dispatcher_interval_seconds: float = Field(default=5.0, gt=0)

    # Reconciler: polling safety net for comments the webhook never delivered.
    reconcile_interval_seconds: int = Field(default=300, ge=60)
    reconcile_lookback_hours: int = Field(default=72, gt=0, le=24 * 7)
    reconcile_max_per_sweep: int = Field(default=30, gt=0)

    # Insights sync: a read-only copy of the account's metrics for the insights page.
    insights_sync_interval_seconds: int = Field(default=3 * 3600, ge=600)
    # Calls to Meta per sync. The first syncs backfill history over several runs.
    insights_max_calls_per_sync: int = Field(default=200, ge=20, le=1000)
    insights_max_media: int = Field(default=500, ge=10, le=5000)
    insights_backfill_days: int = Field(default=90, ge=7, le=730)
    # Comments are read for activity hours and recurring commenters on posts this recent.
    insights_comment_days: int = Field(default=90, ge=7, le=365)
    # Local day boundaries for daily metrics. Default: Brasília (UTC-3, no daylight saving).
    insights_utc_offset_minutes: int = Field(default=-180, ge=-720, le=840)

    # Competitor analysis (optional). Without APIFY_TOKEN the module is off: no loop, and the
    # page says what is missing. Without OPENROUTER_API_KEY profiles are collected but not
    # classified, and the report button is off. Absent means off, never a fallback value.
    apify_token: ApiKey = None
    openrouter_api_key: ApiKey = None
    apify_profile_actor: str = Field(
        default="apify~instagram-profile-scraper", pattern=r"^[\w.-]+~[\w.-]+$"
    )
    competitors_interval_seconds: int = Field(default=120, ge=30)
    # A profile is collected again after this long (each collection is billed by Apify).
    competitors_refresh_hours: int = Field(default=168, ge=6)
    competitors_max: int = Field(default=30, ge=1, le=200)
    # Posts sent to the decision model per loop run, so a first scan spreads its cost.
    competitors_classify_per_run: int = Field(default=60, ge=1, le=1000)
    jev_model: str = Field(default="typesafe/jev-1.14", min_length=3, max_length=128)
    competitors_report_model: str = Field(
        default="anthropic/claude-sonnet-5.5", min_length=3, max_length=128
    )

    # Chat: the outbox loop, and the default model of the AI replies (needs OPENROUTER_API_KEY;
    # the panel can pick another). The AI itself is switched on in the panel, off by default.
    chat_interval_seconds: float = Field(default=2.0, gt=0)
    chat_ai_model: str = Field(default="anthropic/claude-haiku-5.5", min_length=3, max_length=128)

    # Public HTTPS address of this instance (https://insta.example.com). Only used by
    # `cli doctor`, to print and check the URLs to paste in the Meta app. Optional.
    public_url: Annotated[PublicUrl | None, BeforeValidator(_empty_is_unset)] = None

    # Public contact on the privacy, terms and data-deletion pages Meta requires to go Live.
    # Unset: those pages answer 404.
    legal_contact_email: Annotated[ContactEmail | None, BeforeValidator(_empty_is_unset)] = None

    def app_secrets(self) -> tuple[bytes, ...]:
        """Both secrets are accepted for webhook signatures; Meta's docs are ambiguous."""
        return tuple(
            s.get_secret_value().encode() for s in (self.meta_app_secret, self.instagram_app_secret)
        )


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # pydantic's default message echoes the rejected value, which may be a real secret
        # with a typo. Report only the variable name and the reason.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']).upper()}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise SystemExit(f"invalid configuration, refusing to start: {problems}") from None
