"""Job queue on SQLite: atomic claim with a lease, and crash recovery.

Recovery rule: a RUNNING job whose lease is gone either never called Meta (send_started_at is
empty, so it goes back to PENDING) or might have (so it becomes UNCONFIRMED, never resent).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import utcnow
from app.models import DM_JOB_TYPES, Delivery, DeliveryStatus, Job, JobStatus, JobType
from app.ops import OpsLevel, OpsSource, record

LEASE = timedelta(minutes=2)


async def claim_due(
    session_factory: async_sessionmaker[AsyncSession],
    limit: int,
    now: datetime | None = None,
) -> list[int]:
    """Atomically move up to `limit` due jobs to RUNNING. One UPDATE .. RETURNING statement."""
    now = now or utcnow()
    due = (
        select(Job.id)
        .where(Job.status == JobStatus.PENDING, Job.run_at <= now)
        .order_by(Job.run_at, Job.id)
        .limit(limit)
        .scalar_subquery()
    )
    async with session_factory() as session, session.begin():
        result = await session.execute(
            update(Job)
            .where(Job.id.in_(due), Job.status == JobStatus.PENDING)
            .values(status=JobStatus.RUNNING, locked_until=now + LEASE, updated_at=now)
            .returning(Job.id)
        )
        return list(result.scalars().all())


@dataclass(frozen=True)
class RecoveryResult:
    requeued: int
    unconfirmed: int


async def recover_stale(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    at_boot: bool,
    now: datetime | None = None,
) -> RecoveryResult:
    """At boot every RUNNING job is stale (one process). Later, only expired leases are."""
    now = now or utcnow()
    query = select(Job).where(Job.status == JobStatus.RUNNING)
    if not at_boot:
        query = query.where(Job.locked_until < now)
    requeued = unconfirmed = 0
    async with session_factory() as session, session.begin():
        for job in (await session.execute(query)).scalars().all():
            job.locked_until = None
            if job.send_started_at is None:
                job.status = JobStatus.PENDING
                requeued += 1
                continue
            job.status = JobStatus.UNCONFIRMED
            job.last_error = "interrupted after calling Meta; check the inbox before resending"
            unconfirmed += 1
            delivery = await session.get(Delivery, job.delivery_id)
            if delivery is not None:
                mark_unconfirmed(delivery, job.type, job.last_error)
        if unconfirmed:
            record(
                session,
                OpsSource.WORKER,
                OpsLevel.WARNING,
                f"{unconfirmed} send(s) interrupted mid-call are now UNCONFIRMED",
            )
    return RecoveryResult(requeued=requeued, unconfirmed=unconfirmed)


def mark_unconfirmed(delivery: Delivery, job_type: str, reason: str) -> None:
    if job_type in DM_JOB_TYPES:
        delivery.status = DeliveryStatus.UNCONFIRMED
        delivery.error_message = reason
    elif job_type == JobType.FOLLOWUP_DM:
        delivery.followup_status = DeliveryStatus.UNCONFIRMED
        delivery.followup_error = reason
    else:
        delivery.public_reply_status = DeliveryStatus.UNCONFIRMED
        delivery.public_reply_error = reason
