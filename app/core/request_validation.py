"""Request schemas compiled, and request bodies validated, in worker processes.

jsonschema-rs holds the GIL, and what a provider's schema costs, to compile or on a
consumer's body, is not bounded in advance. So both run in worker processes
(`app.core.request_validation_worker`), each within a deadline: a worker that overruns is
killed, and the API process only waits on the worker's pipes, so its event loop never
blocks, whether it is asyncio's or uvloop's. The API process never compiles a schema.

The compile deadline is at most half the validation deadline (Settings), so a stored
schema, which compiled within the compile deadline when it was saved, leaves a worker that
must compile it afresh at least half the validation deadline for the body.
"""

import asyncio
import contextlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol

from app.core.errors import InvalidInputError, UnavailableError
from app.core.json_types import JsonObject
from app.core.logging import get_logger
from app.core.request_validation_worker import (
    ANSWER_HEADER,
    CHECK_SCHEMA,
    NO_REFUSAL,
    REQUEST_HEADER,
    UNCOMPILABLE_SCHEMA,
    VALIDATE_BODY,
)

logger = get_logger(__name__)

REQUEST_BODY_MAX_BYTES = 1024 * 1024
# A worker whose size passes this after an answer exits, and the next call starts a fresh
# one: below the worker's 512 MiB address-space limit on Linux, with room for one more
# request (`app.core.request_validation_worker.MEMORY_LIMIT_BYTES`).
WORKER_RECYCLE_BYTES = 384 * 1024 * 1024
# How long a new worker may take to start (starts take 20 to 160 ms), and a killed one to
# exit.
WORKER_START_TIMEOUT_SECONDS = 2.0
WORKER_EXIT_TIMEOUT_SECONDS = 1.0
# At most one warning per this long about callers turned away as busy.
BUSY_LOG_INTERVAL_SECONDS = 5.0
# A worker's answers are refusals of at most a few hundred characters; a longer one means
# the worker broke the protocol.
ANSWER_MAX_BYTES = 64 * 1024

# Run as a module from the directory that holds the `app` package.
_WORKER_COMMAND = (sys.executable, "-m", "app.core.request_validation_worker")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_KINDS = {CHECK_SCHEMA: "compile", VALIDATE_BODY: "validate"}
# Every 503 the pool raises is a passing condition: busy, starting or shutting down.
_RETRY_AFTER = {"Retry-After": "1"}


class _NotReceivedError(Exception):
    """The worker ended, or never started, before it had read the request."""


class _CrashedError(Exception):
    """The worker ended, or broke the protocol, while it answered the request."""


class _MalformedAnswerError(Exception):
    """An answer the worker should never send."""


class _Answer(NamedTuple):
    outcome: int
    exiting: bool
    text: str


_EMPTY = _Answer(NO_REFUSAL, False, "")


@dataclass(frozen=True, eq=False)
class _Worker:
    process: asyncio.subprocess.Process
    requests: asyncio.StreamWriter
    answers: asyncio.StreamReader


@dataclass(eq=False)
class _Slot:
    """A place for one worker, empty until a caller starts a worker in it."""

    worker: _Worker | None = None


class RequestValidationPool(Protocol):
    """Compiles request schemas and validates request bodies, each within a deadline."""

    async def check_schema(self, schema_json: str) -> str | None:
        """Why the schema `schema_json` does not compile in time, or None when it does."""
        ...

    async def validate(self, schema_json: str, body: bytes) -> str | None:
        """Why `body` does not match the schema `schema_json`, or None when it does.

        Raises InvalidInputError when validating overruns the deadline or ends the worker,
        and UnavailableError when it cannot run now or the schema no longer compiles.
        """
        ...


class ProcessRequestValidationPool:
    """A RequestValidationPool of worker processes.

    A caller waits at most the validation deadline for a free worker. Workers start on
    first use. A worker that overruns, dies, or is still working for a cancelled caller is
    killed, and the next caller in its place starts a new one.
    """

    def __init__(
        self,
        *,
        workers: int,
        timeout_seconds: float,
        compile_timeout_seconds: float,
        recycle_bytes: int = WORKER_RECYCLE_BYTES,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._compile_timeout_seconds = compile_timeout_seconds
        self._recycle_bytes = recycle_bytes
        # The free slots, then None once the pool closes, which each caller passes on.
        self._free: asyncio.Queue[_Slot | None] = asyncio.Queue()
        for _ in range(workers):
            self._free.put_nowait(_Slot())
        # Compiles never hold every worker, so provider saves cannot starve request
        # bodies; with a single worker the two share it.
        self._compiles = asyncio.Semaphore(max(1, workers - 1))
        self._processes: set[asyncio.subprocess.Process] = set()
        self._closed = False
        self._busy_logged_at = float("-inf")

    @property
    def pids(self) -> tuple[int, ...]:
        """The process ids of the workers running."""
        return tuple(process.pid for process in self._processes if process.returncode is None)

    async def check_schema(self, schema_json: str) -> str | None:
        """Why the schema `schema_json` does not compile in time, or None when it does."""
        try:
            await asyncio.wait_for(self._compiles.acquire(), self._timeout_seconds)
        except TimeoutError:
            raise self._busy(CHECK_SCHEMA) from None
        try:
            answer = await self._call(CHECK_SCHEMA, schema_json, b"", self._compile_timeout_seconds)
        except TimeoutError:
            return "request_schema is too expensive to compile"
        except _CrashedError:
            return "request_schema could not be compiled"
        finally:
            self._compiles.release()
        return answer.text or None

    async def validate(self, schema_json: str, body: bytes) -> str | None:
        """Why `body` does not match the schema `schema_json`, or None when it does."""
        try:
            answer = await self._call(VALIDATE_BODY, schema_json, body, self._timeout_seconds)
        except TimeoutError:
            raise InvalidInputError(
                "the request body could not be validated within the time limit",
                problem_type="request_validation_timeout",
            ) from None
        except _CrashedError:
            raise InvalidInputError(
                "the request body could not be validated",
                problem_type="request_validation_failed",
            ) from None
        if answer.outcome == UNCOMPILABLE_SCHEMA:
            # The save check compiled it, so the library or the database changed since:
            # the provider's listing is at fault, and stays so until the schema is saved
            # again, hence the long Retry-After.
            raise UnavailableError(
                "the listing is unavailable: its request schema does not compile",
                problem_type="listing_unavailable",
                headers={"Retry-After": "60"},
            )
        return answer.text or None

    async def close(self) -> None:
        """Fail the calls waiting and running at once, and stop every worker."""
        self._closed = True
        self._free.put_nowait(None)
        processes = tuple(self._processes)
        for process in processes:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        await asyncio.gather(*(_reap(process) for process in processes))

    async def _call(
        self,
        kind: int,
        schema_json: str,
        body: bytes,
        deadline_seconds: float,
    ) -> _Answer:
        self._raise_if_closed()
        try:
            slot = await asyncio.wait_for(self._free.get(), self._timeout_seconds)
        except TimeoutError:
            raise self._busy(kind) from None
        if slot is None:
            self._free.put_nowait(None)
            raise _closing()
        try:
            answer = await self._call_in(slot, kind, schema_json, body, deadline_seconds)
        except TimeoutError:
            _discard(slot)
            logger.warning(
                "request validation worker killed at its deadline",
                extra={"kind": _KINDS[kind], "deadline_seconds": deadline_seconds},
            )
            raise
        except BaseException:
            _discard(slot)
            raise
        else:
            if answer.exiting:
                _discard(slot)
            return answer
        finally:
            if not self._closed:
                self._free.put_nowait(slot)

    async def _call_in(
        self,
        slot: _Slot,
        kind: int,
        schema_json: str,
        body: bytes,
        deadline_seconds: float,
    ) -> _Answer:
        # A worker that ends before it has read the request (killed while idle, or ended
        # while starting) is not the request's doing: try once more on a new one.
        started = time.monotonic()
        cause: BaseException | None = None
        for _ in range(2):
            try:
                worker = slot.worker or await self._start(slot)
                return await self._exchange(worker, kind, schema_json, body, deadline_seconds)
            except _NotReceivedError as exc:
                self._raise_if_closed()
                _discard(slot)
                cause = exc.__cause__
        raise _cannot_start(
            f"it ended before it was ready or had read a request: {cause!r}", started
        )

    async def _start(self, slot: _Slot) -> _Worker:
        """Start a worker in `slot`, and wait until it is ready."""
        self._raise_if_closed()
        self._processes = {process for process in self._processes if process.returncode is None}
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *_WORKER_COMMAND,
                str(self._recycle_bytes),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                cwd=_PROJECT_ROOT,
                # The API's environment (its secrets) is not handed to native code that
                # runs on untrusted input. This is not isolation: the worker runs as the
                # same user, so it could still read /proc/<API pid>/environ. A separate
                # user or a seccomp filter is a follow-up (phase 7).
                env={},
                # Out of the terminal's process group, so that its Ctrl-C does not reach
                # the worker: the pool stops it.
                start_new_session=True,
            )
        except OSError as exc:
            raise _cannot_start(repr(exc), started) from exc
        self._processes.add(process)
        if self._closed:  # close() began while the worker started, and may have missed it
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await _reap(process)
            raise _closing()
        if process.stdin is None or process.stdout is None:  # both piped above: narrows
            msg = "request validation worker has no pipes"
            raise RuntimeError(msg)
        slot.worker = _Worker(process, process.stdin, process.stdout)
        try:
            ready = await asyncio.wait_for(
                _read_answer(slot.worker.answers), WORKER_START_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            # Not retried: a start that hangs once is likely to hang again.
            cause = f"no ready answer within {WORKER_START_TIMEOUT_SECONDS} s"
            raise _cannot_start(cause, started) from exc
        except (asyncio.IncompleteReadError, _MalformedAnswerError) as exc:
            raise _NotReceivedError from exc
        if ready != _EMPTY:
            raise _NotReceivedError
        return slot.worker

    async def _exchange(
        self,
        worker: _Worker,
        kind: int,
        schema_json: str,
        body: bytes,
        deadline_seconds: float,
    ) -> _Answer:
        schema = schema_json.encode()
        try:
            async with asyncio.timeout(deadline_seconds):
                # uvloop raises on a write to a pipe it has closed; asyncio drops it.
                if worker.requests.is_closing():
                    raise _NotReceivedError
                try:
                    worker.requests.writelines(
                        [REQUEST_HEADER.pack(kind, len(schema), len(body)), schema, body]
                    )
                    await worker.requests.drain()
                    read = await _read_answer(worker.answers)
                except (ConnectionError, asyncio.IncompleteReadError) as exc:
                    raise _NotReceivedError from exc
                # An acknowledgement with text would shift every later answer by one.
                if read != _EMPTY:
                    raise _MalformedAnswerError
                with contextlib.suppress(asyncio.IncompleteReadError):
                    return await _read_answer(worker.answers)
        except _MalformedAnswerError:
            logger.warning(
                "request validation worker sent a malformed answer",
                extra={"kind": _KINDS[kind]},
            )
            raise _CrashedError from None
        # The worker ended while it answered: the request's doing, unless the pool closed.
        self._raise_if_closed()
        await _reap(worker.process)
        logger.warning(
            "request validation worker exited",
            extra={"kind": _KINDS[kind], "exitcode": worker.process.returncode},
        )
        raise _CrashedError

    def _busy(self, kind: int) -> UnavailableError:
        now = time.monotonic()
        if now - self._busy_logged_at >= BUSY_LOG_INTERVAL_SECONDS:
            self._busy_logged_at = now
            logger.warning("request validation is busy", extra={"kind": _KINDS[kind]})
        return UnavailableError("request validation is busy; retry shortly", headers=_RETRY_AFTER)

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise _closing()


async def check_request_schema_compiles(*, pool: RequestValidationPool, schema: JsonObject) -> None:
    """Refuse a request schema being saved that does not compile within the deadline.

    The endpoint services call this after the request models' own checks. Raises
    InvalidInputError (422), located at `request_schema` as the request models report
    their own refusals, when the schema does not compile, or does not compile in time.
    Raises UnavailableError (503, with `Retry-After`) when no worker is free within the
    validation deadline, when workers cannot start, or when the pool is closing.
    """
    refusal = await pool.check_schema(_canonical(schema))
    if refusal is not None:
        error: JsonObject = {
            "type": "value_error",
            "loc": ["body", "request_schema"],
            "msg": f"Value error, {refusal}",
        }
        raise InvalidInputError(refusal, extensions={"errors": [error]})


async def validate_request_body(
    *,
    pool: RequestValidationPool,
    schema: JsonObject,
    body: bytes,
) -> None:
    """Validate a raw request body against its endpoint's stored request schema.

    The invoke path calls this with the loaded listing's `request_schema` and the body's
    bytes as received. Raises InvalidInputError (422) when the body is refused: larger than
    REQUEST_BODY_MAX_BYTES, not UTF-8 JSON, nested too deep, or not matching the schema,
    naming its first error and where it is. The problem type is
    `request_validation_timeout` when validating overruns the deadline, and
    `request_validation_failed` when it ends the worker: both are the schema and body's
    doing, and would recur. Raises UnavailableError (503, with `Retry-After`) when no
    worker is free within the deadline, when workers cannot start, when the pool is
    closing, or, as `listing_unavailable`, when the stored schema no longer compiles.
    """
    if len(body) > REQUEST_BODY_MAX_BYTES:
        msg = f"request body must be at most {REQUEST_BODY_MAX_BYTES} bytes"
        raise InvalidInputError(msg)
    refusal = await pool.validate(_canonical(schema), body)
    if refusal is not None:
        raise InvalidInputError(refusal)


def _canonical(schema: JsonObject) -> str:
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _closing() -> UnavailableError:
    return UnavailableError("request validation is shutting down", headers=_RETRY_AFTER)


def _cannot_start(cause: str, started: float) -> UnavailableError:
    logger.error(
        "request validation workers cannot start",
        extra={"cause": cause, "elapsed_seconds": round(time.monotonic() - started, 3)},
    )
    return UnavailableError("request validation is unavailable", headers=_RETRY_AFTER)


async def _reap(process: asyncio.subprocess.Process) -> None:
    """Wait a bounded time for a killed worker to exit, and the event loop to reap it."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), WORKER_EXIT_TIMEOUT_SECONDS)


def _discard(slot: _Slot) -> None:
    """Kill the worker in `slot`, if there is one; the event loop reaps it."""
    if slot.worker is not None:
        with contextlib.suppress(ProcessLookupError):
            slot.worker.process.kill()
        slot.worker = None


async def _read_answer(answers: asyncio.StreamReader) -> _Answer:
    outcome, exiting, size = ANSWER_HEADER.unpack(await answers.readexactly(ANSWER_HEADER.size))
    if size > ANSWER_MAX_BYTES:
        raise _MalformedAnswerError
    try:
        return _Answer(outcome, exiting, (await answers.readexactly(size)).decode())
    except UnicodeDecodeError:
        raise _MalformedAnswerError from None
