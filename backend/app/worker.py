"""Executes jobs: the DM (private reply, direct message, follow-gate link), the optional public
reply, and the read-only follow check.

Order inside a DM, which is what makes crashes and retries safe:
1. check the delivery was not already sent;
2. reserve a rate-limit slot (or reschedule for when one opens);
3. persist send_started_at, then call Meta;
4. explicit rejection: release the slot, retry or fail; anything ambiguous: UNCONFIRMED.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import utcnow
from app.gate import (
    CHECK_RETRY_DELAYS,
    CHECKS_PER_TAP,
    DEFAULT_BUTTON_LABEL,
    RECHECK_DELAYS,
    gate_payload,
)
from app.meta.client import Button, LinkButton, MetaClient, PostbackButton
from app.meta.errors import (
    AmbiguousFailure,
    ConfirmedRejection,
    MetaError,
    NotSent,
    RejectionKind,
)
from app.meta.limits import MAX_ATTEMPTS, MESSAGING_WINDOW, RETRY_BACKOFF
from app.models import (
    DM_JOB_TYPES,
    Account,
    AccountStatus,
    Campaign,
    Delivery,
    DeliveryStatus,
    GateState,
    Job,
    JobStatus,
    JobType,
)
from app.ops import OpsLevel, OpsSource, record
from app.queue import claim_due, mark_unconfirmed, recover_stale
from app.ratelimit import RateLimiter
from app.tokens import TokenDecryptError, cipher_for, mark_token_invalid

logger = logging.getLogger(__name__)

# While the account cannot send (paused, token invalid), jobs wait and are checked again.
ACCOUNT_BLOCKED_RETRY = timedelta(minutes=5)
# Meta's code for an invalid parameter; for a button message it may be the template itself.
INVALID_PARAMETER = 100


def render(template: str, username: str | None) -> str:
    """`{username}` is plain substitution, never template evaluation."""
    return template.replace("{username}", username or "")


def private_reply_content(
    campaign: Campaign, username: str | None, *, text_only: bool
) -> tuple[str, LinkButton | None]:
    text = render(campaign.dm_message, username)
    if campaign.link_url and campaign.link_button_label and not text_only:
        return text, LinkButton(title=campaign.link_button_label, url=campaign.link_url)
    if campaign.link_url and campaign.link_url not in text:
        text = f"{text}\n{campaign.link_url}"
    return text, None


def gate_invite_content(
    campaign: Campaign, username: str | None, delivery_id: int
) -> tuple[str, PostbackButton]:
    """The first DM of a follow-gated campaign: an invite and a button, and never the link."""
    text = render(campaign.follow_gate_message or "", username)
    label = campaign.follow_gate_button_label or DEFAULT_BUTTON_LABEL
    return text, PostbackButton(title=label, payload=gate_payload(delivery_id))


def public_reply_text(campaign: Campaign, delivery: Delivery) -> str:
    messages = campaign.public_reply_messages
    return render(messages[delivery.id % len(messages)], delivery.actor_username)


@dataclass(frozen=True)
class _Context:
    job: Job
    delivery: Delivery
    campaign: Campaign
    account: Account | None
    token: str | None


class Worker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        meta: MetaClient,
        limiter: RateLimiter,
    ) -> None:
        self._sf = session_factory
        self._settings = settings
        self._meta = meta
        self._limiter = limiter

    async def run_until_idle(self) -> int:
        """One loop tick: expire stale leases, then drain everything that is due."""
        await recover_stale(self._sf, at_boot=False)
        processed = 0
        while True:
            job_ids = await claim_due(self._sf, self._settings.worker_concurrency)
            if not job_ids:
                return processed
            await asyncio.gather(*(self._run_safely(job_id) for job_id in job_ids))
            processed += len(job_ids)

    async def _run_safely(self, job_id: int) -> None:
        try:
            await self.process(job_id)
        except Exception:
            # The job stays RUNNING; lease recovery decides its fate from send_started_at.
            logger.exception("worker: job %s crashed", job_id)

    async def process(self, job_id: int) -> None:
        ctx = await self._load(job_id)
        if ctx is None:
            return
        if ctx.job.type == JobType.PRIVATE_REPLY:
            await self._send_dm(ctx, direct=False)
        elif ctx.job.type == JobType.DIRECT_MESSAGE:
            await self._send_dm(ctx, direct=True)
        elif ctx.job.type == JobType.FOLLOW_CHECK:
            await self._follow_check(ctx)
        elif ctx.job.type == JobType.FOLLOWUP_DM:
            await self._send_followup(ctx)
        else:
            await self._public_reply(ctx)

    async def _load(self, job_id: int) -> _Context | None:
        async with self._sf() as session, session.begin():
            job = await session.get(Job, job_id)
            if job is None or job.status != JobStatus.RUNNING:
                return None
            delivery = await session.get(Delivery, job.delivery_id)
            campaign = await session.get(Campaign, delivery.campaign_id) if delivery else None
            if delivery is None or campaign is None:
                job.status = JobStatus.FAILED
                job.last_error = "delivery or campaign missing"
                return None
            account = await session.get(Account, 1)
            token: str | None = None
            if account is not None and account.status == AccountStatus.ACTIVE:
                try:
                    token = cipher_for(self._settings).decrypt(account.token_encrypted)
                except TokenDecryptError as exc:
                    await mark_token_invalid(session, str(exc))
            if account is None or token is None or account.sending_paused:
                job.status = JobStatus.PENDING
                job.run_at = utcnow() + ACCOUNT_BLOCKED_RETRY
                job.locked_until = None
                return None
            return _Context(job, delivery, campaign, account, token)

    # ---- the DM: a private reply to a comment, or a direct message to a story reply ------

    async def _lookup_username(self, ctx: _Context, user_id: str) -> str | None:
        """Best effort. The webhook of a story reply carries no @, and a DM that opens with
        'Oi !' is worse than one short extra call. Any failure just leaves it empty."""
        assert ctx.token is not None  # noqa: S101
        try:
            profile = await self._meta.get_profile(ctx.token, user_id, "username")
        except MetaError:
            return None
        username = profile.get("username")
        if not isinstance(username, str) or not username:
            return None
        async with self._sf() as session, session.begin():
            stored = await session.get(Delivery, ctx.delivery.id)
            if stored is not None:
                stored.actor_username = username
        return username

    async def _send_dm(self, ctx: _Context, *, direct: bool) -> None:
        """Order, which is what makes crashes and retries safe: check the delivery is still
        pending, reserve a slot where one is needed, persist send_started_at, then call Meta."""
        job, delivery = ctx.job, ctx.delivery
        if delivery.status != DeliveryStatus.PENDING or delivery.dm_sent_at is not None:
            await self._finish(job.id, JobStatus.DONE)
            return

        username = delivery.actor_username
        if direct:
            # A direct message is only allowed for 24 h after the person wrote to us.
            if utcnow() - delivery.created_at > MESSAGING_WINDOW:
                await self._fail(job, "24 h messaging window expired before the DM was sent")
                return
            username = username or await self._lookup_username(ctx, delivery.actor_id)
        else:
            next_slot = await self._limiter.reserve(job.id)
            if next_slot is not None:
                await self._reschedule(job.id, next_slot, "hourly limit reached", count=False)
                return

        now = utcnow()
        async with self._sf() as session, session.begin():
            stored = await session.get(Job, job.id)
            stored_delivery = await session.get(Delivery, delivery.id)
            assert stored is not None and stored_delivery is not None  # noqa: S101
            stored.send_started_at = now
            stored.attempts += 1
            stored_delivery.attempts += 1
            attempts = stored.attempts
            text_only = stored.fallback_text

        # A gated campaign sends an invite first. The link waits until Meta says they follow.
        gated = bool(
            delivery.gate_state is not None
            and ctx.campaign.follow_gate_enabled
            and ctx.campaign.follow_gate_message
        )
        button: Button | None
        if gated:
            text, button = gate_invite_content(ctx.campaign, username, delivery.id)
        else:
            text, button = private_reply_content(ctx.campaign, username, text_only=text_only)
        assert ctx.token is not None  # noqa: S101
        try:
            if direct:
                message_id = await self._meta.send_message(
                    ctx.token, delivery.actor_id, text, button
                )
            else:
                message_id = await self._meta.send_private_reply(
                    ctx.token, delivery.source_id, text, button
                )
        except AmbiguousFailure as exc:
            await self._unconfirmed(job, str(exc))
            return
        except NotSent as exc:
            if not direct:
                await self._limiter.release(job.id)
            await self._retry_or_fail(job, attempts, str(exc))
            return
        except ConfirmedRejection as exc:
            if not direct:
                await self._limiter.release(job.id)
            # A refused invite must fail loudly. The text-only retry would drop the button,
            # and a plain text invite cannot be tapped.
            await self._rejected(job, exc, attempts, used_button=button is not None and not gated)
            return

        async with self._sf() as session, session.begin():
            stored_delivery = await session.get(Delivery, delivery.id)
            stored = await session.get(Job, job.id)
            assert stored is not None and stored_delivery is not None  # noqa: S101
            stored_delivery.status = DeliveryStatus.SENT
            stored_delivery.dm_sent_at = utcnow()
            stored_delivery.dm_message_id = message_id
            stored_delivery.error_message = None
            if not gated:
                # The gate was switched off after this delivery was created: the DM above
                # already carried the link, so there is nothing left to wait for.
                stored_delivery.gate_state = None
            stored.status = JobStatus.DONE
            stored.locked_until = None

    async def _rejected(
        self, job: Job, exc: ConfirmedRejection, attempts: int, *, used_button: bool
    ) -> None:
        if exc.kind == RejectionKind.TOKEN_INVALID:
            async with self._sf() as session, session.begin():
                await mark_token_invalid(session, exc.message)
            # Not this job's fault: give the attempt back and wait for a new token.
            await self._reschedule(
                job.id, utcnow() + ACCOUNT_BLOCKED_RETRY, str(exc), count=False, undo_attempt=True
            )
            return
        if exc.kind == RejectionKind.RATE_LIMITED:
            await self._retry_or_fail(job, attempts, str(exc))
            return
        if used_button and exc.code == INVALID_PARAMETER:
            # Maybe the template was refused. The rejection proves nothing left, so resend
            # once with the link in the text.
            async with self._sf() as session, session.begin():
                stored = await session.get(Job, job.id)
                assert stored is not None  # noqa: S101
                if not stored.fallback_text:
                    stored.fallback_text = True
                    stored.status = JobStatus.PENDING
                    stored.run_at = utcnow()
                    stored.send_started_at = None
                    stored.locked_until = None
                    stored.last_error = str(exc)
                    return
        await self._fail(job, str(exc))

    # ---- follow gate: check, then release the link ---------------------------------------

    async def _follow_check(self, ctx: _Context) -> None:
        """Ask Meta whether the person follows. Read only, so a crash simply runs it again.

        Follows: release the link. Meta cannot tell: release it too, because holding it back
        from someone who may already follow is the worse mistake. Does not follow: look again
        after a short wait, since a new follow takes a moment to show, and after the last look
        ask the person once more.
        """
        job, delivery = ctx.job, ctx.delivery
        if delivery.gate_state in (GateState.PASSED, GateState.RELEASED) or (
            delivery.followup_status is not None
        ):
            await self._finish(job.id, JobStatus.DONE)
            return
        if delivery.gate_user_id is None:
            await self._fail(job, "follow check without the id of the person")
            return
        assert ctx.token is not None  # noqa: S101
        async with self._sf() as session, session.begin():
            stored = await session.get(Job, job.id)
            assert stored is not None  # noqa: S101
            stored.attempts += 1
            attempts = stored.attempts

        follows: bool | None = None
        try:
            profile = await self._meta.get_profile(
                ctx.token, delivery.gate_user_id, "is_user_follow_business"
            )
            value = profile.get("is_user_follow_business")
            follows = value if isinstance(value, bool) else None
        except ConfirmedRejection as exc:
            if exc.kind == RejectionKind.TOKEN_INVALID:
                async with self._sf() as session, session.begin():
                    await mark_token_invalid(session, exc.message)
                await self._reschedule(
                    job.id,
                    utcnow() + ACCOUNT_BLOCKED_RETRY,
                    str(exc),
                    count=False,
                    undo_attempt=True,
                )
                return
            if exc.kind == RejectionKind.RATE_LIMITED and await self._retry_check(job, attempts):
                return
            # Meta answered, but with an error (for example no consent for this person):
            # that is "cannot tell", which releases the link like a null value does.
            await self._note(f"follow check for delivery {delivery.id} refused: {exc}")
        except MetaError as exc:  # the call itself failed (network, outage)
            if await self._retry_check(job, attempts, str(exc)):
                return
            await self._note(f"follow check for delivery {delivery.id} kept failing: {exc}")

        if follows is False:
            await self._not_following(ctx)
            return
        state = GateState.PASSED if follows else GateState.RELEASED
        if follows is None:
            await self._note(
                f"follow status of delivery {delivery.id} unknown, link released anyway"
            )
        await self._release(ctx, state)

    async def _retry_check(self, job: Job, attempts: int, reason: str = "") -> bool:
        """Try the lookup again soon. False once the retries are used up."""
        if attempts > len(CHECK_RETRY_DELAYS):
            return False
        await self._reschedule(
            job.id, utcnow() + CHECK_RETRY_DELAYS[attempts - 1], reason or "retrying the lookup"
        )
        return True

    async def _note(self, message: str) -> None:
        async with self._sf() as session, session.begin():
            record(session, OpsSource.WORKER, OpsLevel.INFO, message)

    async def _release(self, ctx: _Context, state: GateState) -> None:
        """Let the link go: queue the follow-up DM once, whatever number of checks agree."""
        job, delivery = ctx.job, ctx.delivery
        async with self._sf() as session, session.begin():
            stored = await session.get(Delivery, delivery.id)
            assert stored is not None  # noqa: S101
            stored.gate_state = state
            stored.followup_status = DeliveryStatus.PENDING
            await session.execute(
                sqlite_insert(Job)
                .values(
                    type=JobType.FOLLOWUP_DM,
                    delivery_id=delivery.id,
                    dedupe_key=f"followup:{delivery.id}",
                    status=JobStatus.PENDING,
                    run_at=utcnow(),
                    attempts=0,
                    fallback_text=False,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                .on_conflict_do_nothing()
            )
            done = await session.get(Job, job.id)
            if done is not None:
                done.status = JobStatus.DONE
                done.locked_until = None

    async def _not_following(self, ctx: _Context) -> None:
        job, delivery = ctx.job, ctx.delivery
        check = int((job.payload or {}).get("check", 1))
        if check < CHECKS_PER_TAP:
            # Not yet: look again shortly. The key is the tap's, with the next check number.
            base = job.dedupe_key.rsplit(":", 1)[0]
            async with self._sf() as session, session.begin():
                await session.execute(
                    sqlite_insert(Job)
                    .values(
                        type=JobType.FOLLOW_CHECK,
                        delivery_id=delivery.id,
                        dedupe_key=f"{base}:{check + 1}",
                        status=JobStatus.PENDING,
                        run_at=utcnow() + RECHECK_DELAYS[check - 1],
                        attempts=0,
                        fallback_text=False,
                        payload={"check": check + 1},
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    .on_conflict_do_nothing()
                )
                done = await session.get(Job, job.id)
                if done is not None:
                    done.status = JobStatus.DONE
                    done.locked_until = None
            return

        # Still not following after the last look: ask again. The button of the invite still
        # works, so a failed reminder costs nothing and is not retried.
        assert ctx.token is not None  # noqa: S101
        assert delivery.gate_user_id is not None  # noqa: S101
        text, button = gate_invite_content(ctx.campaign, delivery.actor_username, delivery.id)
        try:
            await self._meta.send_message(ctx.token, delivery.gate_user_id, text, button)
        except MetaError as exc:
            await self._note(f"reminder for delivery {delivery.id} not sent: {exc}")
        async with self._sf() as session, session.begin():
            stored = await session.get(Delivery, delivery.id)
            if stored is not None:
                stored.gate_state = GateState.REMINDED
        await self._finish(job.id, JobStatus.DONE)

    async def _send_followup(self, ctx: _Context) -> None:
        """The message with the link, after the gate let the person through."""
        job, delivery = ctx.job, ctx.delivery
        if delivery.followup_status != DeliveryStatus.PENDING:
            await self._finish(job.id, JobStatus.DONE)
            return
        assert ctx.token is not None  # noqa: S101
        if delivery.gate_user_id is None:
            await self._fail(job, "follow-up without the id of the person")
            return
        # The tap opened the 24 h window, and it is what counts here.
        opened = delivery.gate_tapped_at or delivery.created_at
        if utcnow() - opened > MESSAGING_WINDOW:
            await self._fail(job, "24 h messaging window expired before the link was sent")
            return
        username = delivery.actor_username or await self._lookup_username(
            ctx, delivery.gate_user_id
        )

        async with self._sf() as session, session.begin():
            stored = await session.get(Job, job.id)
            assert stored is not None  # noqa: S101
            stored.send_started_at = utcnow()
            stored.attempts += 1
            attempts = stored.attempts
            text_only = stored.fallback_text

        text, button = private_reply_content(ctx.campaign, username, text_only=text_only)
        try:
            message_id = await self._meta.send_message(
                ctx.token, delivery.gate_user_id, text, button
            )
        except AmbiguousFailure as exc:
            await self._unconfirmed(job, str(exc))
            return
        except NotSent as exc:
            await self._retry_or_fail(job, attempts, str(exc))
            return
        except ConfirmedRejection as exc:
            await self._rejected(job, exc, attempts, used_button=button is not None)
            return

        async with self._sf() as session, session.begin():
            stored_delivery = await session.get(Delivery, delivery.id)
            stored = await session.get(Job, job.id)
            assert stored is not None and stored_delivery is not None  # noqa: S101
            stored_delivery.followup_status = DeliveryStatus.SENT
            stored_delivery.followup_sent_at = utcnow()
            stored_delivery.followup_message_id = message_id
            stored_delivery.followup_error = None
            stored.status = JobStatus.DONE
            stored.locked_until = None

    # ---- public reply --------------------------------------------------------------------

    async def _public_reply(self, ctx: _Context) -> None:
        job, delivery = ctx.job, ctx.delivery
        if delivery.public_reply_status != DeliveryStatus.PENDING or not (
            ctx.campaign.public_reply_messages
        ):
            await self._finish(job.id, JobStatus.DONE)
            return
        async with self._sf() as session, session.begin():
            stored = await session.get(Job, job.id)
            assert stored is not None  # noqa: S101
            stored.send_started_at = utcnow()
            stored.attempts += 1
            attempts = stored.attempts
        assert ctx.token is not None  # noqa: S101
        try:
            await self._meta.reply_to_comment(
                ctx.token, delivery.source_id, public_reply_text(ctx.campaign, delivery)
            )
        except AmbiguousFailure as exc:
            await self._unconfirmed(job, str(exc))
            return
        except ConfirmedRejection as exc:
            if exc.kind == RejectionKind.TOKEN_INVALID:
                await self._rejected(job, exc, attempts, used_button=False)
            elif exc.kind == RejectionKind.RATE_LIMITED:
                await self._retry_or_fail(job, attempts, str(exc))
            else:
                await self._fail(job, str(exc))
            return
        except MetaError as exc:  # NotSent
            await self._retry_or_fail(job, attempts, str(exc))
            return
        async with self._sf() as session, session.begin():
            stored_delivery = await session.get(Delivery, delivery.id)
            stored = await session.get(Job, job.id)
            assert stored is not None and stored_delivery is not None  # noqa: S101
            stored_delivery.public_reply_status = DeliveryStatus.SENT
            stored_delivery.public_reply_sent_at = utcnow()
            stored.status = JobStatus.DONE
            stored.locked_until = None

    # ---- state transitions ---------------------------------------------------------------

    async def _finish(self, job_id: int, status: JobStatus) -> None:
        async with self._sf() as session, session.begin():
            stored = await session.get(Job, job_id)
            if stored is not None:
                stored.status = status
                stored.locked_until = None

    async def _reschedule(
        self,
        job_id: int,
        run_at: datetime,
        reason: str,
        *,
        count: bool = True,
        undo_attempt: bool = False,
    ) -> None:
        async with self._sf() as session, session.begin():
            stored = await session.get(Job, job_id)
            if stored is None:
                return
            stored.status = JobStatus.PENDING
            stored.run_at = run_at
            stored.locked_until = None
            # Cleared so a crash before the next call is not mistaken for an ambiguous send.
            stored.send_started_at = None
            stored.last_error = reason
            if undo_attempt and stored.attempts > 0:
                stored.attempts -= 1

    async def _retry_or_fail(self, job: Job, attempts: int, reason: str) -> None:
        if attempts >= MAX_ATTEMPTS:
            await self._fail(job, f"gave up after {attempts} attempts: {reason}")
            return
        await self._reschedule(job.id, utcnow() + RETRY_BACKOFF[attempts - 1], reason)

    async def _fail(self, job: Job, reason: str) -> None:
        async with self._sf() as session, session.begin():
            stored = await session.get(Job, job.id)
            delivery = await session.get(Delivery, job.delivery_id)
            if stored is None or delivery is None:
                return
            stored.status = JobStatus.FAILED
            stored.locked_until = None
            stored.last_error = reason
            if job.type in DM_JOB_TYPES:
                delivery.status = DeliveryStatus.FAILED
                delivery.error_message = reason
            elif job.type == JobType.FOLLOWUP_DM:
                delivery.followup_status = DeliveryStatus.FAILED
                delivery.followup_error = reason
            else:
                delivery.public_reply_status = DeliveryStatus.FAILED
                delivery.public_reply_error = reason

    async def _unconfirmed(self, job: Job, reason: str) -> None:
        async with self._sf() as session, session.begin():
            stored = await session.get(Job, job.id)
            delivery = await session.get(Delivery, job.delivery_id)
            if stored is None or delivery is None:
                return
            stored.status = JobStatus.UNCONFIRMED
            stored.locked_until = None
            stored.last_error = reason
            mark_unconfirmed(delivery, job.type, reason)
            record(
                session,
                OpsSource.WORKER,
                OpsLevel.WARNING,
                f"delivery {delivery.id} unconfirmed ({job.type}); "
                "check the inbox before resending",
            )
