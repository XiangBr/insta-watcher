"""Dashboard login throttle. The dashboard checks the password; this module only counts failures.

Keys are opaque to the backend: the dashboard sends an HMAC of the client IP, never the IP.
A global counter also grows with every failure, so spreading guesses over many IPs still
slows down. The price is that an attacker can delay the owner's login; the CLI keeps working.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import utcnow
from app.models import LoginAttempt

GLOBAL_KEY = "global"  # shorter than any client key, so it never collides with one


@dataclass(frozen=True)
class Policy:
    free_failures: int
    max_delay: timedelta


CLIENT_POLICY = Policy(free_failures=3, max_delay=timedelta(minutes=15))
GLOBAL_POLICY = Policy(free_failures=20, max_delay=timedelta(minutes=15))
# A key with no failure for this long starts over.
FORGET_AFTER = timedelta(hours=24)


def delay_for(failures: int, policy: Policy) -> timedelta:
    """Doubles from 1 s after the free failures, capped."""
    over = failures - policy.free_failures
    if over <= 0:
        return timedelta(0)
    return min(timedelta(seconds=2 ** min(over - 1, 20)), policy.max_delay)


def _retry_after(row: LoginAttempt | None, now: datetime) -> int:
    if row is None or row.blocked_until is None or row.blocked_until <= now:
        return 0
    return max(1, int((row.blocked_until - now).total_seconds() + 0.999))


def _fresh(row: LoginAttempt | None, now: datetime) -> LoginAttempt | None:
    return row if row is not None and now - row.last_failure_at < FORGET_AFTER else None


async def check(
    session_factory: async_sessionmaker[AsyncSession], key: str, now: datetime | None = None
) -> int:
    """Seconds the caller must wait before trying a password. 0 means go ahead."""
    now = now or utcnow()
    async with session_factory() as session:
        client = _fresh(await session.get(LoginAttempt, key), now)
        everyone = _fresh(await session.get(LoginAttempt, GLOBAL_KEY), now)
    return max(_retry_after(client, now), _retry_after(everyone, now))


async def record_failure(
    session_factory: async_sessionmaker[AsyncSession], key: str, now: datetime | None = None
) -> int:
    """Count a wrong password. Returns the wait before the next attempt."""
    now = now or utcnow()
    async with session_factory() as session, session.begin():
        waits = [
            await _bump(session, key, CLIENT_POLICY, now),
            await _bump(session, GLOBAL_KEY, GLOBAL_POLICY, now),
        ]
    return max(waits)


async def _bump(session: AsyncSession, key: str, policy: Policy, now: datetime) -> int:
    row = await session.get(LoginAttempt, key)
    if row is None:
        row = LoginAttempt(key=key, failures=0, last_failure_at=now)
        session.add(row)
    elif _fresh(row, now) is None:
        row.failures = 0
    row.failures += 1
    row.last_failure_at = now
    delay = delay_for(row.failures, policy)
    row.blocked_until = now + delay if delay else None
    return _retry_after(row, now)


async def record_success(session_factory: async_sessionmaker[AsyncSession], key: str) -> None:
    """The client key starts over. The global counter only decays with time."""
    async with session_factory() as session, session.begin():
        await session.execute(delete(LoginAttempt).where(LoginAttempt.key == key))


async def prune_login_attempts(
    session_factory: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> int:
    now = now or utcnow()
    async with session_factory() as session, session.begin():
        result = await session.execute(
            delete(LoginAttempt).where(LoginAttempt.last_failure_at < now - FORGET_AFTER)
        )
    return result.rowcount or 0  # type: ignore[attr-defined]
