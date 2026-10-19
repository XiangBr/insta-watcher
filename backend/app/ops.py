"""Operational events for the dashboard. Callers must never pass secrets or user text."""

import logging
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OpsEvent

logger = logging.getLogger(__name__)


class OpsSource(StrEnum):
    WORKER = "WORKER"
    TOKEN = "TOKEN"  # noqa: S105 (event source name, not a secret)
    RECONCILER = "RECONCILER"
    WEBHOOK = "WEBHOOK"
    SYSTEM = "SYSTEM"


class OpsLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


_LOG_LEVEL = {OpsLevel.INFO: logging.INFO, OpsLevel.WARNING: logging.WARNING}


def record(
    session: AsyncSession,
    source: OpsSource,
    level: OpsLevel,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Add an ops event to the caller's transaction and mirror it to the log."""
    session.add(OpsEvent(source=source, level=level, message=message, payload=payload))
    logger.log(_LOG_LEVEL.get(level, logging.ERROR), "[%s] %s", source, message)
