from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import login_throttle as lt
from app.db import create_engine, create_session_factory, utcnow


@pytest.fixture
async def sf(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(database_url)
    yield create_session_factory(engine)
    await engine.dispose()


def test_delay_doubles_after_free_failures_and_is_capped() -> None:
    policy = lt.CLIENT_POLICY
    assert [lt.delay_for(n, policy).total_seconds() for n in range(1, 8)] == [
        0,
        0,
        0,
        1,
        2,
        4,
        8,
    ]
    assert lt.delay_for(500, policy) == policy.max_delay


async def test_client_is_blocked_after_free_failures(
    sf: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    for _ in range(lt.CLIENT_POLICY.free_failures):
        assert await lt.record_failure(sf, "a" * 20, now) == 0
    assert await lt.record_failure(sf, "a" * 20, now) == 1
    assert await lt.check(sf, "a" * 20, now) == 1
    assert await lt.check(sf, "a" * 20, now + timedelta(seconds=2)) == 0
    assert await lt.check(sf, "b" * 20, now) == 0  # another client is unaffected


async def test_failures_spread_over_many_clients_hit_the_global_limit(
    sf: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    for i in range(lt.GLOBAL_POLICY.free_failures + 1):
        await lt.record_failure(sf, f"client-{i:04d}-xxxxxxxx", now)
    assert await lt.check(sf, "never-tried-before-xxx", now) >= 1


async def test_failures_are_forgotten_after_a_quiet_day(
    sf: async_sessionmaker[AsyncSession],
) -> None:
    then = utcnow() - lt.FORGET_AFTER - timedelta(minutes=1)
    for _ in range(10):
        await lt.record_failure(sf, "a" * 20, then)
    now = utcnow()
    assert await lt.check(sf, "a" * 20, now) == 0
    # The count starts over instead of continuing from 10.
    assert await lt.record_failure(sf, "a" * 20, now) == 0
    assert await lt.prune_login_attempts(sf, now) == 0  # both rows were just touched
    assert await lt.prune_login_attempts(sf, now + lt.FORGET_AFTER + timedelta(seconds=1)) == 2


async def test_success_clears_only_the_client(sf: async_sessionmaker[AsyncSession]) -> None:
    now = utcnow()
    for _ in range(5):
        await lt.record_failure(sf, "a" * 20, now)
    await lt.record_success(sf, "a" * 20)
    assert await lt.check(sf, "a" * 20, now) == 0
    async with sf() as session:
        assert (await session.get(lt.LoginAttempt, lt.GLOBAL_KEY)) is not None
