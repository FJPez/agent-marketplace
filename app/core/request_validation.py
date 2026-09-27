"""Request bodies validated against their endpoint's request schema, in worker processes.

jsonschema-rs holds the GIL while it validates, and what a provider's schema costs on a
consumer's body is not bounded in advance. So request bodies are validated in worker
processes (`app.core.request_validation_worker`), each validation within a deadline: a
worker that overruns is killed, and the API process only waits on the worker's pipes,
without blocking its event loop, whether that loop is asyncio's or uvloop's. The API
process never holds a compiled validator.

The invoke path calls `validate_request_body(pool=..., schema=..., body=...)` with the raw
request body, at most REQUEST_BODY_MAX_BYTES of it. The pool is
`Resources.request_validation_pool`. The call raises:
- InvalidInputError (422) when the body is refused: too large, not JSON, nested too deep,
  or not matching the schema (naming the first error and where it is);
- InvalidInputError with the problem type `request_validation_timeout` when validating
  overruns the deadline, and `request_validation_failed` when it ends the worker (both are
  the schema and body's doing, and would recur);
- UnavailableError (503) when no worker is free within the deadline (with `Retry-After`),
  when workers cannot start, and when the pool is closing.
"""

import asyncio
import contextlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import InvalidInputError, UnavailableError
from app.core.json_types import JsonObject
from app.core.logging import get_logger
from app.core.request_validation_worker import ANSWER_HEADER, REQUEST_HEADER

logger = get_logger(__name__)

REQUEST_BODY_MAX_BYTES = 1024 * 1024
# How long a new worker may take to start, and a killed one to exit.
WORKER_START_TIMEOUT_SECONDS = 10.0
WORKER_EXIT_TIMEOUT_SECONDS = 1.0

# Run as a module from the directory that holds the `app` package.
_WORKER_COMMAND = (sys.executable, "-m", "app.core.request_validation_worker")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TIME_LIMIT = "the request body could not be validated within the time limit"
_FAILED = "the request body could not be validated"
_BUSY = "request validation is busy; retry shortly"
_UNAVAILABLE = "request validation is unavailable"
_CLOSING = "request validation is shutting down"


class _NotReceivedError(Exception):
    """The worker ended, or never started, before it had read the request."""


class _CrashedError(Exception):
    """The worker ended while it answered the request."""


@dataclass(frozen=True, eq=False)
class _Worker:
    process: asyncio.subprocess.Process
    requests: asyncio.StreamWriter
    answers: asyncio.StreamReader


@dataclass(eq=False)
class _Slot:
    """A place for one worker, empty until a caller starts a worker in it."""

    worker: _Worker | None = None


class ProcessRequestValidationPool:
    """Worker processes validating request bodies, each validation within a deadline.

    A caller waits at most the deadline for a free worker. Workers start on first use. A
    worker that overruns, dies, or is still working for a cancelled caller is killed, and
    the next caller in its place starts a new one.
    """

    def __init__(self, *, workers: int, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds
        # The free slots, then None once the pool closes, which each caller passes on.
        self._free: asyncio.Queue[_Slot | None] = asyncio.Queue()
        for _ in range(workers):
            self._free.put_nowait(_Slot())
        self._processes: set[asyncio.subprocess.Process] = set()
        self._closed = False

    @property
    def pids(self) -> tuple[int, ...]:
        """The process ids of the workers running."""
        return tuple(process.pid for process in self._processes if process.returncode is None)

    async def validate(self, schema_json: str, body: bytes) -> str | None:
        """Why `body` does not match the schema `schema_json`, or None when it does."""
        schema = schema_json.encode()
        request = REQUEST_HEADER.pack(len(schema), len(body)) + schema + body
        try:
            return await self._call(request, self._timeout_seconds)
        except TimeoutError:
            raise InvalidInputError(
                _TIME_LIMIT, problem_type="request_validation_timeout"
            ) from None
        except _CrashedError:
            raise InvalidInputError(_FAILED, problem_type="request_validation_failed") from None

    async def close(self) -> None:
        """Fail the calls waiting and running at once, and stop every worker."""
        self._closed = True
        self._free.put_nowait(None)
        for process in self._processes:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        for process in self._processes:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), WORKER_EXIT_TIMEOUT_SECONDS)

    async def _call(self, request: bytes, deadline_seconds: float) -> str | None:
        self._raise_if_closed()
        try:
            slot = await asyncio.wait_for(self._free.get(), self._timeout_seconds)
        except TimeoutError:
            raise UnavailableError(_BUSY, headers={"Retry-After": "1"}) from None
        if slot is None:
            self._free.put_nowait(None)
            raise UnavailableError(_CLOSING)
        try:
            return await self._call_in(slot, request, deadline_seconds)
        except BaseException:
            _discard(slot)
            raise
        finally:
            if not self._closed:
                self._free.put_nowait(slot)

    async def _call_in(self, slot: _Slot, request: bytes, deadline_seconds: float) -> str | None:
        # A worker that ends before it has read the request (killed while idle, or unable
        # to start) is not the request's doing: try once more on a new one.
        for _ in range(2):
            try:
                worker = slot.worker or await self._start(slot)
                return await self._exchange(worker, request, deadline_seconds)
            except _NotReceivedError:
                self._raise_if_closed()
                _discard(slot)
        logger.error("request validation workers cannot start or read requests")
        raise UnavailableError(_UNAVAILABLE)

    async def _start(self, slot: _Slot) -> _Worker:
        """Start a worker in `slot`, and wait until it is ready."""
        self._raise_if_closed()
        self._processes = {process for process in self._processes if process.returncode is None}
        try:
            process = await asyncio.create_subprocess_exec(
                *_WORKER_COMMAND,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                cwd=_PROJECT_ROOT,
                # None of the API's environment (its secrets) reaches native code that
                # runs on untrusted input.
                env={},
                # Out of the terminal's process group, so that its Ctrl-C does not reach
                # the worker: the pool stops it.
                start_new_session=True,
            )
        except OSError as exc:
            raise _NotReceivedError from exc
        self._processes.add(process)
        if process.stdin is None or process.stdout is None:  # both piped above: narrows
            msg = "request validation worker has no pipes"
            raise RuntimeError(msg)
        slot.worker = _Worker(process, process.stdin, process.stdout)
        try:
            await asyncio.wait_for(_read_answer(slot.worker.answers), WORKER_START_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.IncompleteReadError) as exc:
            raise _NotReceivedError from exc
        return slot.worker

    async def _exchange(
        self, worker: _Worker, request: bytes, deadline_seconds: float
    ) -> str | None:
        async with asyncio.timeout(deadline_seconds):
            try:
                # uvloop raises on a write to a pipe it has closed; asyncio drops it.
                if worker.requests.is_closing():
                    raise ConnectionResetError
                worker.requests.write(request)
                await worker.requests.drain()
                await _read_answer(worker.answers)  # it has read the request
            except (ConnectionError, asyncio.IncompleteReadError) as exc:
                raise _NotReceivedError from exc
            with contextlib.suppress(asyncio.IncompleteReadError):
                return await _read_answer(worker.answers)
        # The worker ended while it answered: the request's doing, unless the pool closed.
        self._raise_if_closed()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(worker.process.wait(), WORKER_EXIT_TIMEOUT_SECONDS)
        logger.warning(
            "request validation worker exited",
            extra={"exitcode": worker.process.returncode},
        )
        raise _CrashedError

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise UnavailableError(_CLOSING)


async def validate_request_body(
    *,
    pool: ProcessRequestValidationPool,
    schema: JsonObject,
    body: bytes,
) -> None:
    """Validate a raw JSON request body against its endpoint's accepted request schema.

    Raises the errors of the module docstring.
    """
    if len(body) > REQUEST_BODY_MAX_BYTES:
        msg = f"request body must be at most {REQUEST_BODY_MAX_BYTES} bytes"
        raise InvalidInputError(msg)
    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    refusal = await pool.validate(schema_json, body)
    if refusal is not None:
        raise InvalidInputError(refusal)


def _discard(slot: _Slot) -> None:
    """Kill the worker in `slot`, if there is one; the event loop reaps it."""
    if slot.worker is not None:
        with contextlib.suppress(ProcessLookupError):
            slot.worker.process.kill()
        slot.worker = None


async def _read_answer(answers: asyncio.StreamReader) -> str | None:
    (size,) = ANSWER_HEADER.unpack(await answers.readexactly(ANSWER_HEADER.size))
    return (await answers.readexactly(size)).decode() or None
