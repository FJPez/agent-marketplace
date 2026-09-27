"""Request bodies validated in worker processes, each validation within a deadline."""

import asyncio
import json
import multiprocessing
import os
import re
import signal
from collections.abc import Iterator
from multiprocessing.process import BaseProcess
from typing import Protocol

import pytest

from app.core.errors import InvalidInputError
from app.core.json_types import JsonObject
from app.core.request_body_validation import (
    REQUEST_BODY_ERROR_TEXT_MAX_LENGTH,
    REQUEST_BODY_MAX_BYTES,
    REQUEST_BODY_MAX_DEPTH,
    RequestValidationPool,
    validate_request_body,
)

# The workers of one test would compete for CPU with another's: run them one at a time.
pytestmark = pytest.mark.xdist_group("request_validation_pool")

# Only a pathological body overruns this, however busy the machine is.
DEADLINE_SECONDS = 5.0
# For the tests that wait for a deadline to pass.
SHORT_DEADLINE_SECONDS = 0.25
# Each of 100,000 items is compared with 10,000 enum entries: seconds of work.
SLOW_SCHEMA: JsonObject = {"items": {"not": {"enum": [[] for _ in range(10_000)]}}}
SLOW_BODY = json.dumps([[0]] * 100_000).encode()
TIME_LIMIT = "the request body could not be validated within the time limit"
BUSY = "the request body could not be validated: no validation worker was free in time"
FAILED = "the request body could not be validated"
MISMATCH = "request body does not match the request schema"
NOT_JSON = "request body is not valid JSON"
TOO_DEEP = f"request body must nest at most {REQUEST_BODY_MAX_DEPTH} levels"
CUT = REQUEST_BODY_ERROR_TEXT_MAX_LENGTH


class OpenPool(Protocol):
    def __call__(
        self,
        *,
        workers: int = 2,
        timeout_seconds: float = DEADLINE_SECONDS,
    ) -> RequestValidationPool: ...


@pytest.fixture
def open_pool() -> Iterator[OpenPool]:
    pools: list[RequestValidationPool] = []

    def open_pool(
        *,
        workers: int = 2,
        timeout_seconds: float = DEADLINE_SECONDS,
    ) -> RequestValidationPool:
        pool = RequestValidationPool(workers=workers, timeout_seconds=timeout_seconds)
        pools.append(pool)
        return pool

    yield open_pool
    for pool in pools:
        pool.close()


def _worker_pids(before: set[BaseProcess]) -> set[int]:
    """The ids of this process's running children started since `before`."""
    return {
        process.pid
        for process in multiprocessing.active_children()
        if process not in before and process.pid is not None
    }


def _exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize(
    ("schema", "body"),
    [
        pytest.param(
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            b'{"text": "hello", "extra": [1, 2.5, null, true, {"k": "v"}]}',
            id="object",
        ),
        pytest.param(
            {"type": "array", "items": {"type": "integer"}},
            b"[" + b"0," * ((REQUEST_BODY_MAX_BYTES - 4) // 2) + b"0] ",
            id="largest",
        ),
        pytest.param(
            {},
            b"[" * REQUEST_BODY_MAX_DEPTH + b"]" * REQUEST_BODY_MAX_DEPTH,
            id="deepest",
        ),
    ],
)
async def test_a_body_matching_the_schema_is_accepted(
    open_pool: OpenPool,
    schema: JsonObject,
    body: bytes,
) -> None:
    await validate_request_body(pool=open_pool(), schema=schema, body=body)


@pytest.mark.parametrize(
    ("schema", "body", "message"),
    [
        pytest.param(
            {"properties": {"a/b": {"items": {"type": "integer"}}}},
            b'{"a/b": [1, "x"]}',
            f'{MISMATCH}: "x" is not of type "integer" at /a~1b/1',
            id="first_error_located",
        ),
        pytest.param(
            {"type": "object"},
            b"5",
            f'{MISMATCH}: 5 is not of type "object" at the root',
            id="at_the_root",
        ),
        pytest.param(
            {"items": {"type": "integer"}},
            json.dumps(["x" * 10_000]).encode(),
            f'{MISMATCH}: "{"x" * (CUT - 1)}... at /0',
            id="long_message_cut",
        ),
        pytest.param(
            {"additionalProperties": {"type": "integer"}},
            json.dumps({"k" * 10_000: "x"}).encode(),
            f'{MISMATCH}: "x" is not of type "integer" at /{"k" * (CUT - 1)}...',
            id="long_location_cut",
        ),
        pytest.param({}, b'{"a": ', NOT_JSON, id="malformed"),
        pytest.param({}, b"[NaN]", NOT_JSON, id="nan"),
        pytest.param({}, b'"\xff"', NOT_JSON, id="not_utf_8"),
        # jsonschema-rs reads a string's text, and so meets its lone surrogate, only when
        # a keyword needs it: here, to describe the error.
        pytest.param(
            {"items": {"type": "integer"}},
            b'["\\ud800"]',
            "request body holds a string that is not valid Unicode (a lone surrogate)",
            id="lone_surrogate",
        ),
        pytest.param(
            {},
            b"[" * (REQUEST_BODY_MAX_DEPTH + 1) + b"]" * (REQUEST_BODY_MAX_DEPTH + 1),
            TOO_DEEP,
            id="too_deep",
        ),
        pytest.param({}, b"[" * 100_000 + b"]" * 100_000, TOO_DEEP, id="far_too_deep"),
    ],
)
async def test_a_body_is_refused_naming_the_problem(
    open_pool: OpenPool,
    schema: JsonObject,
    body: bytes,
    message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=f"^{re.escape(message)}$"):
        await validate_request_body(pool=open_pool(), schema=schema, body=body)


async def test_an_oversized_body_is_refused_before_any_worker_starts(open_pool: OpenPool) -> None:
    before = set(multiprocessing.active_children())

    with pytest.raises(
        InvalidInputError,
        match=f"^request body must be at most {REQUEST_BODY_MAX_BYTES} bytes$",
    ):
        await validate_request_body(
            pool=open_pool(),
            schema={},
            body=b" " * (REQUEST_BODY_MAX_BYTES + 1),
        )

    assert _worker_pids(before) == set()


async def test_a_validation_past_the_deadline_is_refused_and_its_worker_replaced(
    open_pool: OpenPool,
) -> None:
    pool = open_pool(workers=1, timeout_seconds=SHORT_DEADLINE_SECONDS)
    before = set(multiprocessing.active_children())
    await validate_request_body(pool=pool, schema={}, body=b"{}")
    (overrunning,) = _worker_pids(before)

    with pytest.raises(InvalidInputError, match=f"^{TIME_LIMIT}$"):
        await validate_request_body(pool=pool, schema=SLOW_SCHEMA, body=SLOW_BODY)

    assert not _exists(overrunning)
    await validate_request_body(pool=pool, schema={"type": "object"}, body=b"{}")
    (replacement,) = _worker_pids(before)
    assert replacement != overrunning


async def test_a_worker_that_dies_is_replaced(
    open_pool: OpenPool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = open_pool(workers=1)
    before = set(multiprocessing.active_children())
    await validate_request_body(pool=pool, schema={}, body=b"{}")
    (dying,) = _worker_pids(before)
    validation = asyncio.create_task(
        validate_request_body(pool=pool, schema=SLOW_SCHEMA, body=SLOW_BODY)
    )
    await asyncio.sleep(0)  # the body is sent, and the worker is validating it

    os.kill(dying, signal.SIGKILL)

    with pytest.raises(InvalidInputError, match=f"^{FAILED}$"):
        await validation
    (record,) = [
        record for record in caplog.records if record.name == "app.core.request_body_validation"
    ]
    assert (record.levelname, record.getMessage()) == (
        "WARNING",
        "request validation worker exited",
    )
    assert vars(record)["exitcode"] == -signal.SIGKILL
    await validate_request_body(pool=pool, schema={"type": "object"}, body=b"{}")


@pytest.mark.parametrize(
    ("workers", "messages"),
    [
        pytest.param(2, [TIME_LIMIT, TIME_LIMIT], id="each_on_its_own_worker"),
        # The first holds the only worker while it starts and then for the deadline; the
        # second gives up waiting for it after the deadline.
        pytest.param(1, [TIME_LIMIT, BUSY], id="the_second_waits_at_most_the_deadline"),
    ],
)
async def test_validations_run_in_parallel_up_to_the_number_of_workers(
    open_pool: OpenPool,
    workers: int,
    messages: list[str],
) -> None:
    pool = open_pool(workers=workers, timeout_seconds=SHORT_DEADLINE_SECONDS)

    results = await asyncio.gather(
        validate_request_body(pool=pool, schema=SLOW_SCHEMA, body=SLOW_BODY),
        validate_request_body(pool=pool, schema=SLOW_SCHEMA, body=SLOW_BODY),
        return_exceptions=True,
    )

    assert [str(result) for result in results] == messages
    assert all(isinstance(result, InvalidInputError) for result in results)


async def test_closing_the_pool_during_a_validation_starts_no_replacement(
    open_pool: OpenPool,
) -> None:
    pool = open_pool(workers=1, timeout_seconds=SHORT_DEADLINE_SECONDS)
    before = set(multiprocessing.active_children())
    await validate_request_body(pool=pool, schema={}, body=b"{}")
    validation = asyncio.create_task(
        validate_request_body(pool=pool, schema=SLOW_SCHEMA, body=SLOW_BODY)
    )
    await asyncio.sleep(0)

    pool.close()

    with pytest.raises(InvalidInputError):
        await validation
    assert _worker_pids(before) == set()
