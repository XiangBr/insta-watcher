"""Alembic environment. Reads only DATABASE_URL, so migrations do not need the app secrets."""

import asyncio
from logging.config import fileConfig
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import Connection

from alembic import context
from app.db import UTCDateTime, create_engine
from app.models import Base


class _DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = Field(min_length=1)


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _render_item(type_: str, obj: object, autogen_context: Any) -> str | Literal[False]:
    # Migrations store UTCDateTime as its underlying fixed-width string.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.String(27)"
    return False


def _url() -> str:
    # Tests pass the URL through the Alembic config; everything else uses the environment.
    configured = config.get_main_option("sqlalchemy.url")
    return configured or _DatabaseSettings().database_url  # type: ignore[call-arg]


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        render_as_batch=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
