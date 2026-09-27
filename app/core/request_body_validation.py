"""Request bodies validated against their endpoint's request schema, in worker processes.

jsonschema-rs holds the GIL while it validates, and what a provider's schema costs on a
consumer's body is not bounded in advance. So request bodies are validated in a pool of
worker processes, each validation within a deadline: a worker that overruns it is killed
and replaced, and the API process only waits on a pipe. The API process never holds a
compiled validator. Each worker compiles a schema on first use, as the save check does
(`compile_request_schema`), and keeps the validators it used most recently.

Workers start with the spawn method, which imports the program's main module in each one
unless it runs as `python -m`: that module must guard its entry point with
`if __name__ == "__main__"`, as the uvicorn and pytest scripts do.
"""

import asyncio
import contextlib
import json
import multiprocessing
import resource
import signal
import sys
from dataclasses import dataclass
from functools import lru_cache
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess

import jsonschema_rs

from app.core.errors import InvalidInputError
from app.core.json_types import JsonObject, JsonValue
from app.core.logging import get_logger
from app.core.request_schema_validation import compile_request_schema, json_pointer

logger = get_logger(__name__)

REQUEST_BODY_MAX_BYTES = 1024 * 1024
# Far deeper than real bodies, and far below the stack overflow seen validating about
# 30,000 levels (json.loads itself stops near 10,000).
REQUEST_BODY_MAX_DEPTH = 128
# The most characters of a body's error message, and of its location, a refusal repeats.
REQUEST_BODY_ERROR_TEXT_MAX_LENGTH = 200
WORKER_VALIDATOR_CACHE_SIZE = 256
# Each worker's address space, enforced on Linux only: macOS does not enforce RLIMIT_AS.
WORKER_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
# How long a new worker may take to start, and a killed one to exit.
WORKER_START_TIMEOUT_SECONDS = 10.0
WORKER_EXIT_TIMEOUT_SECONDS = 1.0

_MISMATCH = "request body does not match the request schema"
_NOT_JSON = "request body is not valid JSON"
_LONE_SURROGATE = "request body holds a string that is not valid Unicode (a lone surrogate)"
_TOO_DEEP = f"request body must nest at most {REQUEST_BODY_MAX_DEPTH} levels"
_TIME_LIMIT = "the request body could not be validated within the time limit"
_BUSY = "the request body could not be validated: no validation worker was free in time"
_FAILED = "the request body could not be validated"


@dataclass(eq=False)
class _Worker:
    process: BaseProcess
    connection: Connection
    # Whether it has said it is ready, and so reads what it is sent at once.
    ready: bool = False


class RequestValidationPool:
    """Worker processes validating request bodies, each validation within a deadline.

    The workers start on first use. A caller waits at most the deadline for a free worker,
    then at most the deadline again for its result. A worker that overruns, dies, or is
    still working for a cancelled caller is killed and replaced.
    """

    def __init__(self, *, workers: int, timeout_seconds: float) -> None:
        self._size = workers
        self._timeout_seconds = timeout_seconds
        self._context = multiprocessing.get_context("spawn")
        self._idle: asyncio.Queue[_Worker] = asyncio.Queue()
        self._workers: set[_Worker] = set()
        self._closed = False

    async def validate(self, schema_json: str, body: bytes) -> str | None:
        """Why `body` does not match the schema `schema_json`, or None when it does."""
        if not self._workers and not self._closed:
            for _ in range(self._size):
                self._idle.put_nowait(self._start())
        try:
            worker = await asyncio.wait_for(self._idle.get(), self._timeout_seconds)
        except TimeoutError:
            raise InvalidInputError(_BUSY) from None
        try:
            refusal = await self._run(worker, schema_json, body)
        except BaseException:
            self._replace(worker)
            raise
        self._idle.put_nowait(worker)
        return refusal

    def close(self) -> None:
        """Kill every worker, waiting a bounded time for each to exit."""
        self._closed = True
        for worker in list(self._workers):
            self._stop(worker)

    async def _run(self, worker: _Worker, schema_json: str, body: bytes) -> str | None:
        try:
            if not worker.ready:
                await _receive(worker.connection, WORKER_START_TIMEOUT_SECONDS)
                worker.ready = True
            worker.connection.send((schema_json, body))
            return await _receive(worker.connection, self._timeout_seconds)
        except TimeoutError:
            raise InvalidInputError(_TIME_LIMIT) from None
        except (EOFError, OSError):
            worker.process.join(WORKER_EXIT_TIMEOUT_SECONDS)
            logger.warning(
                "request validation worker exited",
                extra={"exitcode": worker.process.exitcode},
            )
            raise InvalidInputError(_FAILED) from None

    def _start(self) -> _Worker:
        connection, worker_connection = self._context.Pipe()
        process = self._context.Process(
            target=_serve,
            args=(worker_connection,),
            name="request-validation",
            daemon=True,
        )
        process.start()
        # Only the worker holds its end, so its exit reads as the end of the pipe here.
        worker_connection.close()
        worker = _Worker(process, connection)
        self._workers.add(worker)
        return worker

    def _replace(self, worker: _Worker) -> None:
        self._stop(worker)
        if not self._closed:
            self._idle.put_nowait(self._start())

    def _stop(self, worker: _Worker) -> None:
        worker.process.kill()
        worker.process.join(WORKER_EXIT_TIMEOUT_SECONDS)
        worker.connection.close()
        self._workers.discard(worker)


async def validate_request_body(
    *,
    pool: RequestValidationPool,
    schema: JsonObject,
    body: bytes,
) -> None:
    """Validate a raw JSON request body against its endpoint's accepted request schema.

    Raises InvalidInputError when the body is too large, is not JSON, nests too deep or
    does not match the schema (naming its first error and where it is), or when it could
    not be validated within the pool's deadline.
    """
    if len(body) > REQUEST_BODY_MAX_BYTES:
        msg = f"request body must be at most {REQUEST_BODY_MAX_BYTES} bytes"
        raise InvalidInputError(msg)
    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    refusal = await pool.validate(schema_json, body)
    if refusal is not None:
        raise InvalidInputError(refusal)


async def _receive(connection: Connection, timeout_seconds: float) -> str | None:
    """A worker's next message, once it arrives within `timeout_seconds`."""
    loop = asyncio.get_running_loop()
    readable: asyncio.Future[None] = loop.create_future()
    fileno = connection.fileno()
    loop.add_reader(fileno, _resolve, readable)
    try:
        await asyncio.wait_for(readable, timeout_seconds)
    finally:
        loop.remove_reader(fileno)
    return connection.recv()


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


def _serve(connection: Connection) -> None:
    """A worker: answer each (schema, body) the pool sends until the pool hangs up."""
    # The pool stops its workers; a terminal's Ctrl-C reaches them too, and must not.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if sys.platform == "linux":
        # A lower limit already set bounds the worker more tightly.
        with contextlib.suppress(ValueError):
            resource.setrlimit(
                resource.RLIMIT_AS,
                (WORKER_MEMORY_LIMIT_BYTES, WORKER_MEMORY_LIMIT_BYTES),
            )
    validator = lru_cache(maxsize=WORKER_VALIDATOR_CACHE_SIZE)(_compile)
    connection.send(None)  # ready
    while True:
        try:
            schema_json, body = connection.recv()
        except EOFError:
            return
        connection.send(_refusal(validator(schema_json), body))


def _compile(schema_json: str) -> jsonschema_rs.Draft202012Validator:
    return compile_request_schema(json.loads(schema_json))


def _refusal(validator: jsonschema_rs.Draft202012Validator, body: bytes) -> str | None:
    """Why `body` is refused, or None when it matches."""
    try:
        instance = json.loads(body, parse_constant=_refuse_constant)
    except RecursionError:
        return _TOO_DEEP
    except ValueError:  # malformed, not UTF-8, NaN or Infinity, or an over-long integer
        return _NOT_JSON
    if _nests_deeper_than(instance, REQUEST_BODY_MAX_DEPTH):
        return _TOO_DEEP
    try:
        validator.validate(instance)
    except jsonschema_rs.ValidationError as exc:
        location = json_pointer(exc.instance_path) or "the root"
        return f"{_MISMATCH}: {_cut(exc.message)} at {_cut(location)}"
    except UnicodeEncodeError:
        return _LONE_SURROGATE
    return None


def _refuse_constant(name: str) -> None:
    msg = f"{name} is not JSON"
    raise ValueError(msg)


def _nests_deeper_than(instance: JsonValue, depth: int) -> bool:
    containers = [instance] if isinstance(instance, dict | list) else []
    for _ in range(depth):
        containers = [
            member
            for container in containers
            for member in (container.values() if isinstance(container, dict) else container)
            if isinstance(member, dict | list)
        ]
    return bool(containers)


def _cut(text: str) -> str:
    if len(text) <= REQUEST_BODY_ERROR_TEXT_MAX_LENGTH:
        return text
    return f"{text[:REQUEST_BODY_ERROR_TEXT_MAX_LENGTH]}..."
