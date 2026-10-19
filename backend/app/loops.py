"""Background loops started in the FastAPI lifespan. One process, so one copy of each loop."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def run_periodic(
    name: str,
    interval_seconds: float,
    step: Callable[[], Awaitable[None]],
    *,
    wake: asyncio.Event | None = None,
) -> None:
    """Run `step` forever, every `interval_seconds` or as soon as `wake` is set.

    A failing step is logged and retried on the next tick; it never kills the loop.
    """
    while True:
        try:
            await step()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("loop %s: step failed", name)
        if wake is None:
            await asyncio.sleep(interval_seconds)
            continue
        try:
            await asyncio.wait_for(wake.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
        wake.clear()


class LoopGroup:
    """Owns the background tasks and cancels them together on shutdown."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def start(self, name: str, coro: Awaitable[None]) -> None:
        self._tasks.append(asyncio.create_task(coro, name=name))  # type: ignore[arg-type]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
