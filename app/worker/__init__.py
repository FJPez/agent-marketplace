"""The background worker process: `python -m app.worker`.

It opens the same resources as the API (without importing FastAPI or `app.main`) and
runs every registered loop concurrently until SIGTERM or SIGINT. Several worker
processes may run at once, so a loop must claim the rows it works on (`FOR UPDATE SKIP
LOCKED` plus the row's lease and fence) before acting on them.
"""

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.resources import Resources, open_resources

logger = get_logger(__name__)
_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


@dataclass(frozen=True, slots=True)
class Loop:
    """An iteration the worker runs repeatedly, waiting `interval_seconds` after each.

    An iteration calls a plain service function that processes one bounded batch.
    """

    name: str
    iteration: Callable[[Resources], Awaitable[None]]
    interval_seconds: float


# The recovery and reconciliation loops are registered here from phase 5 on.
LOOPS: tuple[Loop, ...] = ()


async def _run_loop(loop: Loop, resources: Resources, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await loop.iteration(resources)
        except Exception:
            logger.exception("worker loop iteration failed", extra={"loop": loop.name})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=loop.interval_seconds)


async def run_worker(settings: Settings, loops: Sequence[Loop]) -> None:
    """Run `loops` until SIGTERM or SIGINT, then close the resources.

    On the signal no loop starts another iteration. An iteration already running gets
    `worker_shutdown_timeout_seconds` to finish and is cancelled after that.
    """
    stop = asyncio.Event()
    event_loop = asyncio.get_running_loop()
    for signum in _STOP_SIGNALS:
        event_loop.add_signal_handler(signum, stop.set)
    try:
        async with open_resources(settings) as resources:
            tasks = [asyncio.create_task(_run_loop(loop, resources, stop)) for loop in loops]
            logger.info("worker started", extra={"loops": [loop.name for loop in loops]})
            await stop.wait()
            logger.info("worker stopping")
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=settings.worker_shutdown_timeout_seconds,
                )
            except TimeoutError:
                logger.warning("worker loops cancelled after the shutdown timeout")
    finally:
        for signum in _STOP_SIGNALS:
            event_loop.remove_signal_handler(signum)
    logger.info("worker stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(run_worker(settings, LOOPS))
    except Exception:
        # Logged as a redacted JSON line, where the interpreter would print a plain traceback.
        logger.exception("worker crashed")
        raise SystemExit(1) from None
