"""Async SQLite engine, session factory and the UTC timestamp column type."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, event
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.types import TypeDecorator

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA foreign_keys=ON",
)

# Fixed width so lexical order equals chronological order in SQL.
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as fixed-width ISO strings in UTC. Rejects naive values."""

    impl = String(27)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed; use UTC-aware values")
        return value.astimezone(UTC).strftime(_TS_FORMAT)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return datetime.strptime(value, _TS_FORMAT).replace(tzinfo=UTC)


def create_engine(database_url: str) -> AsyncEngine:
    # hide_parameters keeps bound values (webhook payloads, comment text) out of errors and logs.
    engine = create_async_engine(database_url, hide_parameters=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        for pragma in _PRAGMAS:
            cursor.execute(pragma)
        cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
