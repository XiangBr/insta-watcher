"""`python -m app.cli doctor`: what is set up, what is missing, and the next step for each.

Meant for people and for coding agents doing the setup: every line says what to do next, and
the exit code is non-zero while something required is missing. Read only, except for one
`/me` call to Meta (and two requests to PUBLIC_URL, when set). Never prints a secret.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import utcnow
from app.meta.client import MetaClient
from app.meta.errors import MetaError
from app.models import AccountStatus, Campaign, Flow, WebhookEvent
from app.tokens import TokenDecryptError, load_token


class Level(StrEnum):
    OK = "ok"
    WARN = "!!"
    FAIL = "XX"
    INFO = "--"


@dataclass(frozen=True)
class Check:
    level: Level
    title: str
    detail: str = ""
    next_step: str = ""


META_URLS = ("/webhook", "/privacy", "/terms", "/data-deletion")


async def _reachable(http: httpx.AsyncClient, url: str) -> int | None:
    try:
        response = await http.get(url, timeout=10.0)
    except httpx.HTTPError:
        return None
    return response.status_code


async def run_checks(
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
    meta: MetaClient,
    http: httpx.AsyncClient,
) -> list[Check]:
    checks: list[Check] = [Check(Level.OK, "configuration loaded", "secrets present and strong")]

    if settings.legal_contact_email:
        checks.append(Check(Level.OK, "legal pages on", "/privacy, /terms and /data-deletion"))
    else:
        checks.append(
            Check(
                Level.FAIL,
                "legal pages off",
                "Meta will not publish the app (Live) without them",
                "set LEGAL_CONTACT_EMAIL in .env and restart: docker compose up -d",
            )
        )

    base = settings.public_url
    if base:
        urls = "  ".join(f"{base}{path}" for path in META_URLS)
        checks.append(Check(Level.INFO, "URLs for the Meta app", urls))
        health = await _reachable(http, f"{base}/health")
        if health == 200:
            checks.append(Check(Level.OK, "public URL answers", f"{base}/health"))
        else:
            checks.append(
                Check(
                    Level.WARN,
                    "public URL did not answer from inside the container",
                    f"{base}/health -> {health or 'no connection'}",
                    "open it in a browser; if it works there, your network just blocks the "
                    "loopback and this can be ignored. Otherwise see docs/deploy.md",
                )
            )
    else:
        checks.append(
            Check(
                Level.WARN,
                "PUBLIC_URL not set",
                "Meta needs a public https address to send webhooks",
                "set PUBLIC_URL=https://your.domain in .env (./scripts/setup.sh does it)",
            )
        )

    async with sf() as session:
        try:
            loaded = await load_token(session, settings)
        except TokenDecryptError:
            loaded = None
            checks.append(
                Check(
                    Level.FAIL,
                    "stored token cannot be decrypted",
                    "ENCRYPTION_KEY changed after the token was stored",
                    "paste the token again: ./scripts/iw set-token",
                )
            )
        last_webhook = await session.scalar(select(func.max(WebhookEvent.received_at)))
        campaigns = await session.scalar(
            select(func.count())
            .select_from(Campaign)
            .where(Campaign.is_active.is_(True), Campaign.deleted_at.is_(None))
        )
        flows = await session.scalar(
            select(func.count())
            .select_from(Flow)
            .where(Flow.is_active.is_(True), Flow.deleted_at.is_(None))
        )

    if loaded is None:
        if not any(c.title.startswith("stored token") for c in checks):
            checks.append(
                Check(
                    Level.FAIL,
                    "no Instagram token",
                    "generate it in the Meta app (docs/meta-setup.md, step 5)",
                    "./scripts/iw set-token  (or paste it in the dashboard, Settings)",
                )
            )
    else:
        account, token = loaded
        if account.status != AccountStatus.ACTIVE:
            checks.append(
                Check(
                    Level.FAIL,
                    "Meta refused the stored token",
                    f"@{account.username}",
                    "generate a new one and run ./scripts/iw set-token",
                )
            )
        else:
            try:
                me = await meta.me(token)
                checks.append(
                    Check(Level.OK, "token valid", f"@{me.get('username', account.username)}")
                )
            except MetaError as exc:
                checks.append(
                    Check(
                        Level.FAIL,
                        "token check failed",
                        str(exc),
                        "generate a new one and run ./scripts/iw set-token",
                    )
                )
            if account.token_expires_at is not None:
                days = (account.token_expires_at - utcnow()).days
                level = Level.OK if days > 10 else Level.WARN
                checks.append(
                    Check(level, "token expiry", f"{days} days left (renewed automatically)")
                )
        if account.webhook_subscribed_at is None:
            checks.append(
                Check(
                    Level.FAIL,
                    "account not subscribed to webhooks",
                    "without it Meta sends nothing, even with the webhook verified",
                    "./scripts/iw subscribe  (or the button in the dashboard, Settings)",
                )
            )
        else:
            checks.append(Check(Level.OK, "account subscribed to webhooks"))
        if account.sending_paused:
            checks.append(
                Check(Level.WARN, "sending paused", "", "./scripts/iw resume (or Settings)")
            )

    if last_webhook is None:
        checks.append(
            Check(
                Level.WARN,
                "no webhook received yet",
                "comment on one of your posts from another account to test",
                "if nothing arrives: app in Live mode? callback URL and fields set? account "
                "subscribed? (docs/meta-setup.md, 'Nada chega')",
            )
        )
    else:
        age = utcnow() - last_webhook
        level = Level.OK if age < timedelta(days=7) else Level.INFO
        checks.append(Check(level, "last webhook received", f"{last_webhook:%Y-%m-%d %H:%M} UTC"))

    checks.append(
        Check(
            Level.INFO,
            "automation",
            f"{campaigns or 0} active campaign(s), {flows or 0} active flow(s)",
            "" if (campaigns or flows) else "create one in the dashboard",
        )
    )
    optional = []
    optional.append("Apify " + ("on" if settings.apify_token else "off"))
    optional.append("OpenRouter " + ("on" if settings.openrouter_api_key else "off"))
    checks.append(Check(Level.INFO, "optional modules", ", ".join(optional)))
    return checks


def render(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        line = f"[{check.level}] {check.title}"
        if check.detail:
            line += f": {check.detail}"
        lines.append(line)
        if check.next_step:
            lines.append(f"     next: {check.next_step}")
    return "\n".join(lines)


def failed(checks: list[Check]) -> bool:
    return any(c.level == Level.FAIL for c in checks)
