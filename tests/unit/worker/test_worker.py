import asyncio
import logging
import signal
import time

import pytest

from app.core.config import Settings
from app.core.resources import Resources
from app.worker import Loop, run_worker


async def test_worker_logs_a_failing_iteration_and_keeps_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def flaky_iteration(resources: Resources) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("iteration exploded")
        signal.raise_signal(signal.SIGTERM)

    with caplog.at_level(logging.ERROR, logger="app.worker"):
        await run_worker(Settings(), [Loop("flaky", flaky_iteration, interval_seconds=0)])

    assert calls == 2
    (record,) = [record for record in caplog.records if record.name == "app.worker"]
    assert record.getMessage() == "worker loop iteration failed"
    assert getattr(record, "loop", None) == "flaky"
    assert "RuntimeError: iteration exploded" in caplog.text


@pytest.mark.parametrize(
    ("iteration_seconds", "shutdown_timeout_seconds", "outcome"),
    [
        pytest.param(0.2, 5.0, "completed", id="finishes_within_the_timeout"),
        pytest.param(5.0, 0.2, "cancelled", id="cancelled_after_the_timeout"),
    ],
)
async def test_worker_stops_on_sigterm_during_an_iteration(
    iteration_seconds: float,
    shutdown_timeout_seconds: float,
    outcome: str,
) -> None:
    outcomes: list[str] = []

    async def slow_iteration(resources: Resources) -> None:
        signal.raise_signal(signal.SIGTERM)
        try:
            await asyncio.sleep(iteration_seconds)
        except asyncio.CancelledError:
            outcomes.append("cancelled")
            raise
        outcomes.append("completed")

    settings = Settings(worker_shutdown_timeout_seconds=shutdown_timeout_seconds)
    started = time.monotonic()

    await run_worker(settings, [Loop("slow", slow_iteration, interval_seconds=0)])

    # One iteration only: the worker starts no new iteration after the signal.
    assert outcomes == [outcome]
    assert time.monotonic() - started < 2
