"""Admin CLI: setup checks and operation from the terminal, next to the dashboard.

Run it with `./scripts/iw <command>` (docker compose exec api python -m app.cli <command>).
The token is only ever read with getpass (no echo, not in shell history or the process list)
and then stored encrypted; every other command uses the stored token.
"""

import argparse
import asyncio
import getpass
import json
import re
import sys
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy import select, update

from app.campaigns import CampaignInput, create_campaign, list_campaigns, set_campaign_state
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.doctor import failed, render, run_checks
from app.meta.client import SUBSCRIBED_FIELDS, MetaClient, PostbackButton
from app.meta.errors import MetaError
from app.models import Account, Delivery, MatchMode, WebhookEvent
from app.status import collect_status, summarize_webhook_payload
from app.tokens import (
    DifferentAccountError,
    clean_pasted_token,
    load_token,
    set_account_token,
)

PROBE_TEXT = "Teste do insta-watcher: toque no botão abaixo para confirmar que o toque chega."
_TOKEN_CHARS = re.compile(r"^[A-Za-z0-9_\-|.]+$")


def describe_token(token: str) -> str:
    """Safe-to-print shape of a token: length and type prefix, never the secret part."""
    shape = f"length={len(token)}, starts_with={token[:4]!r}"
    if not _TOKEN_CHARS.match(token):
        shape += ", contains unexpected characters"
    if token.startswith("EAA"):
        shape += " (Facebook token: use one generated under 'API setup with Instagram login')"
    return shape


class Cli:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = create_engine(settings.database_url)
        self.sf = create_session_factory(self.engine)
        self.http = httpx.AsyncClient(follow_redirects=False)
        self.meta = MetaClient(self.http, settings.meta_graph_api_version)

    async def close(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()

    async def stored_token(self) -> str:
        async with self.sf() as session:
            loaded = await load_token(session, self.settings)
        if loaded is None:
            raise SystemExit("no token stored yet: run `set-token` first")
        return loaded[1]

    async def doctor(self) -> int:
        checks = await run_checks(self.sf, self.settings, self.meta, self.http)
        print(render(checks))
        return 1 if failed(checks) else 0

    # ---- account ---------------------------------------------------------------------------

    async def set_token(self) -> int:
        token = clean_pasted_token(getpass.getpass("Instagram access token (input hidden): "))
        if not token:
            print("empty token", file=sys.stderr)
            return 1
        try:
            info = await set_account_token(self.sf, self.settings, self.meta, token)
        except MetaError as exc:
            print(f"Meta refused the token: {exc}\ntoken received: {describe_token(token)}")
            return 1
        except DifferentAccountError as exc:
            print(str(exc))
            return 1
        print(f"token stored (encrypted) for @{info.username} (ig_user_id {info.ig_user_id})")
        return 0

    async def subscribe(self) -> int:
        token = await self.stored_token()
        try:
            print(json.dumps(await self.meta.subscribe(token, SUBSCRIBED_FIELDS)))
        except MetaError as exc:
            print(f"subscribe failed: {exc}")
            return 1
        return 0

    async def set_paused(self, paused: bool) -> int:
        async with self.sf() as session, session.begin():
            result = await session.execute(
                update(Account).where(Account.id == 1).values(sending_paused=paused)
            )
        if not result.rowcount:  # type: ignore[attr-defined]
            print("no account yet: run `set-token` first")
            return 1
        print("sending PAUSED: nothing will be sent" if paused else "sending resumed")
        return 0

    async def status(self) -> int:
        print(json.dumps(await collect_status(self.sf, self.settings), indent=2))
        return 0

    async def posts(self, limit: int) -> int:
        token = await self.stored_token()
        try:
            media = await self.meta.list_media(token, limit=limit)
        except MetaError as exc:
            print(f"failed: {exc}")
            return 1
        for item in media:
            caption = " ".join(str(item.get("caption") or "").split())[:60]
            print(f"{item.get('id')}  {item.get('timestamp', '')[:10]}  {caption}")
            print(f"    {item.get('permalink', '')}")
        return 0

    # ---- campaigns -------------------------------------------------------------------------

    async def campaign_create(self, args: argparse.Namespace) -> int:
        try:
            data = CampaignInput(
                name=args.name,
                keywords=args.keyword,
                match_mode=MatchMode.CONTAINS if args.mode == "contains" else MatchMode.WHOLE_WORD,
                post_id=args.post_id,
                match_any_post=args.any_post,
                trigger_comments=not args.no_comments,
                trigger_story_replies=args.story_replies,
                dm_message=args.message.replace("\\n", "\n"),
                link_url=args.link,
                link_button_label=args.button,
                public_reply_messages=args.public_reply or [],
                is_active=not args.inactive,
            )
        except ValidationError as exc:
            for error in exc.errors(include_input=False, include_url=False):
                where = ".".join(str(p) for p in error["loc"]) or "campaign"
                print(f"invalid {where}: {error['msg']}")
            return 1
        campaign = await create_campaign(self.sf, data)
        print(f"campaign {campaign.id} created ({'active' if campaign.is_active else 'paused'})")
        return 0

    async def campaign_list(self) -> int:
        async with self.sf() as session:
            campaigns = await list_campaigns(session)
        if not campaigns:
            print("no campaigns")
        for c in campaigns:
            targets = []
            if c.trigger_comments:
                targets.append("any post" if c.match_any_post else f"post {c.post_id}")
            if c.trigger_story_replies:
                targets.append("story replies")
            target = " + ".join(targets)
            state = "ACTIVE" if c.is_active else "paused"
            print(f"[{c.id}] {state:6} {c.name} | {target} | {', '.join(c.keywords)}")
            print(f"      DM: {c.dm_message[:70]!r}" + (f" + {c.link_url}" if c.link_url else ""))
        return 0

    async def campaign_state(self, campaign_id: int, action: str) -> int:
        campaign = await set_campaign_state(
            self.sf,
            campaign_id,
            active={"pause": False, "resume": True}.get(action),
            delete=action == "delete",
        )
        if campaign is None:
            print(f"campaign {campaign_id} not found")
            return 1
        print(f"campaign {campaign_id}: {action}d")
        return 0

    # ---- probes: what does Meta really answer? ---------------------------------------------

    async def _probe_target(self, delivery_id: int) -> Delivery | None:
        async with self.sf() as session:
            delivery = await session.get(Delivery, delivery_id)
        if delivery is None:
            print(f"delivery {delivery_id} not found")
        elif delivery.anonymized_at is not None:
            print(f"delivery {delivery_id} is past retention: the person is no longer identifiable")
            return None
        return delivery

    async def follow_status(self, delivery_id: int) -> int:
        """Show the raw answer of the User Profile API for the person behind a delivery."""
        delivery = await self._probe_target(delivery_id)
        if delivery is None:
            return 1
        token = await self.stored_token()
        fields = "username,is_user_follow_business,is_business_follow_user"
        try:
            profile = await self.meta.get_profile(token, delivery.actor_id, fields)
        except MetaError as exc:
            print(f"Meta refused: {exc}")
            return 1
        # "(absent)" means Meta left the key out, which is different from an explicit null.
        shown = {name: profile.get(name, "(absent)") for name in fields.split(",")}
        line = {"delivery": delivery_id, "trigger": delivery.trigger, **shown}
        print(json.dumps(line, ensure_ascii=False))
        return 0

    async def probe_postback(self, delivery_id: int) -> int:
        """DM the person behind a delivery a button that reports back when tapped."""
        delivery = await self._probe_target(delivery_id)
        if delivery is None:
            return 1
        token = await self.stored_token()
        button = PostbackButton(title="Já segui", payload=f"bw:probe:{delivery_id}")
        try:
            message_id = await self.meta.send_message(token, delivery.actor_id, PROBE_TEXT, button)
        except MetaError as exc:
            print(f"Meta refused: {exc}")
            return 1
        print(f"sent {message_id}. Tap the button, then open Diagnostics > Webhooks received.")
        return 0

    # ---- inspection ------------------------------------------------------------------------

    async def deliveries(self, limit: int, status: str | None, show_text: bool) -> int:
        query = select(Delivery).order_by(Delivery.id.desc()).limit(limit)
        if status:
            query = query.where(Delivery.status == status.upper())
        async with self.sf() as session:
            rows = (await session.execute(query)).scalars().all()
        for d in rows:
            line: dict[str, Any] = {
                "id": d.id,
                "at": d.created_at.isoformat(timespec="seconds"),
                "campaign": d.campaign_id,
                "trigger": d.trigger,
                "origin": d.origin,
                "user": d.actor_username,
                "keyword": d.matched_keyword,
                "dm": d.status,
                "public": d.public_reply_status,
                "error": d.error_message,
            }
            if show_text:
                line["text"] = d.text
            print(json.dumps({k: v for k, v in line.items() if v is not None}, ensure_ascii=False))
        return 0

    async def events(self, limit: int, show_text: bool) -> int:
        async with self.sf() as session:
            rows = (
                await session.execute(
                    select(WebhookEvent).order_by(WebhookEvent.id.desc()).limit(limit)
                )
            ).scalars()
            for event in rows:
                out = {
                    "id": event.id,
                    "received_at": event.received_at.isoformat(timespec="seconds"),
                    "status": event.status,
                    "changes": summarize_webhook_payload(event.payload, with_text=show_text),
                }
                print(json.dumps(out, ensure_ascii=False))
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check the setup and print the next step for each gap")
    sub.add_parser("set-token", help="validate and store the Instagram token (prompted)")
    sub.add_parser("subscribe", help="subscribe the account to comments and messages webhooks")
    sub.add_parser("status", help="queue, rate limit, token and account state")
    sub.add_parser("pause", help="stop all sending (kill switch)")
    sub.add_parser("resume", help="resume sending")
    posts = sub.add_parser("posts", help="recent posts, to pick a post id")
    posts.add_argument("--limit", type=int, default=10)

    campaign = sub.add_parser("campaign", help="manage campaigns")
    csub = campaign.add_subparsers(dest="action", required=True)
    create = csub.add_parser("create", help="create a campaign")
    create.add_argument("--name", required=True)
    create.add_argument("--keyword", action="append", required=True, help="repeat for more")
    create.add_argument("--mode", choices=["whole", "contains"], default="whole")
    target = create.add_mutually_exclusive_group()
    target.add_argument("--post-id", help="answer comments on this post")
    target.add_argument("--any-post", action="store_true", help="answer comments on any post")
    create.add_argument(
        "--story-replies",
        action="store_true",
        help="also answer replies to your stories that contain a keyword",
    )
    create.add_argument(
        "--no-comments",
        action="store_true",
        help="answer story replies only (no --post-id or --any-post)",
    )
    create.add_argument("--message", required=True, help="DM text; {username} and \\n allowed")
    create.add_argument("--link", help="https link sent with the DM")
    create.add_argument("--button", help="button title for the link (max 20 chars)")
    create.add_argument("--public-reply", action="append", help="repeat for rotation")
    create.add_argument("--inactive", action="store_true", help="create paused")
    csub.add_parser("list", help="list campaigns")
    for action in ("pause", "resume", "delete"):
        cmd = csub.add_parser(action, help=f"{action} a campaign")
        cmd.add_argument("id", type=int)

    follow = sub.add_parser(
        "follow-status", help="ask Meta whether the person of a delivery follows the account"
    )
    follow.add_argument("delivery_id", type=int, help="the id shown by 'deliveries'")
    postback = sub.add_parser(
        "probe-postback",
        help="DM a test button to the person of a delivery (needs the 24 h window open)",
    )
    postback.add_argument("delivery_id", type=int, help="the id shown by 'deliveries'")

    deliveries = sub.add_parser("deliveries", help="recent deliveries (no comment text)")
    deliveries.add_argument("--limit", type=int, default=20)
    deliveries.add_argument("--status", help="PENDING, SENT, FAILED or UNCONFIRMED")
    deliveries.add_argument("--show-text", action="store_true", help="include comment text")

    events = sub.add_parser("events", help="recent raw webhook events (no comment text)")
    events.add_argument("--limit", type=int, default=10)
    events.add_argument("--show-text", action="store_true", help="include comment text")
    return parser


async def run(args: argparse.Namespace) -> int:
    cli = Cli(get_settings())
    try:
        match args.command:
            case "doctor":
                return await cli.doctor()
            case "set-token":
                return await cli.set_token()
            case "subscribe":
                return await cli.subscribe()
            case "status":
                return await cli.status()
            case "pause" | "resume":
                return await cli.set_paused(args.command == "pause")
            case "posts":
                return await cli.posts(args.limit)
            case "campaign" if args.action == "create":
                return await cli.campaign_create(args)
            case "campaign" if args.action == "list":
                return await cli.campaign_list()
            case "campaign":
                return await cli.campaign_state(args.id, args.action)
            case "follow-status":
                return await cli.follow_status(args.delivery_id)
            case "probe-postback":
                return await cli.probe_postback(args.delivery_id)
            case "deliveries":
                return await cli.deliveries(args.limit, args.status, args.show_text)
            case "events":
                return await cli.events(args.limit, args.show_text)
        return 1
    finally:
        await cli.close()


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
