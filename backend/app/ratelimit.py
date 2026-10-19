"""Sliding-window limit for private replies, stored in SQLite so a restart does not reset it.

The count and the reservation run under an asyncio lock inside one transaction. That is enough
because the app runs as a single process (--workers 1); a second process would need a DB lock.
"""

import asyncio
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import utcnow
from app.meta.limits import RATE_WINDOW
from app.models import SendEvent

PRIVATE_REPLY = "private_reply"


class RateLimiter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], limit: int) -> None:
        self._session_factory = session_factory
        self._limit = limit
        self._lock = asyncio.Lock()

    async def reserve(self, job_id: int, now: datetime | None = None) -> datetime | None:
        """Take a slot for `job_id`. Returns None if reserved, or when the next slot opens."""
        now = now or utcnow()
        window_start = now - RATE_WINDOW
        async with self._lock, self._session_factory() as session, session.begin():
            used, oldest = (
                await session.execute(
                    select(func.count(SendEvent.id), func.min(SendEvent.sent_at)).where(
                        SendEvent.kind == PRIVATE_REPLY, SendEvent.sent_at > window_start
                    )
                )
            ).one()
            if used < self._limit:
                session.add(SendEvent(kind=PRIVATE_REPLY, job_id=job_id, sent_at=now))
                return None
        # min() keeps the column type, so `oldest` is an aware datetime.
        next_slot: datetime = oldest + RATE_WINDOW
        return next_slot

    async def release(self, job_id: int) -> None:
        """Give the slot back after an explicit rejection: that message did not leave."""
        async with self._lock, self._session_factory() as session, session.begin():
            latest = (
                await session.execute(
                    select(func.max(SendEvent.id)).where(SendEvent.job_id == job_id)
                )
            ).scalar_one_or_none()
            if latest is not None:
                await session.execute(delete(SendEvent).where(SendEvent.id == latest))

    async def used(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        async with self._session_factory() as session:
            return int(
                (
                    await session.execute(
                        select(func.count(SendEvent.id)).where(
                            SendEvent.kind == PRIVATE_REPLY, SendEvent.sent_at > now - RATE_WINDOW
                        )
                    )
                ).scalar_one()
            )

    @property
    def limit(self) -> int:
        return self._limit


async def prune_send_events(
    session_factory: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> int:
    """Rows older than two windows are never read again."""
    now = now or utcnow()
    async with session_factory() as session, session.begin():
        result = await session.execute(
            delete(SendEvent).where(SendEvent.sent_at < now - 2 * RATE_WINDOW)
        )
    return result.rowcount or 0  # type: ignore[attr-defined]
