"""Request schemas compiled, and request bodies validated, in worker processes under
deadlines.

Every test runs on asyncio's event loop and on uvloop, which uvicorn uses in production.
"""

import asyncio
import json
import os
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

import pytest
import uvloop

import app.core.request_validation as request_validation
from app.core.config import Settings
from app.core.errors import InvalidInputError, UnavailableError
from app.core.json_types import JsonObject
from app.core.request_validation import (
    REQUEST_BODY_MAX_BYTES,
    ProcessRequestValidationPool,
    check_request_schema_compiles,
    validate_request_body,
)
from app.core.request_validation_worker import MEMORY_LIMIT_BYTES
from app.core.resources import open_resources

# The workers of one test would compete for CPU with another's: run them one at a time.
pytestmark = pytest.mark.xdist_group("request_validation_pool")

# Only a pathological body overruns this, however busy the machine is.
DEADLINE_SECONDS = 5.0
# For the tests that wait for a deadline to pass.
SHORT_DEADLINE_SECONDS = 0.25
# The default compile deadline: ordinary schemas compile in a few milliseconds.
COMPILE_DEADLINE_SECONDS = 0.1
# Each of 100,000 items is compared with 10,000 enum entries: seconds of work.
SLOW_SCHEMA: JsonObject = {"items": {"not": {"enum": [[] for _ in range(10_000)]}}}
SLOW_BODY = json.dumps([[0]] * 100_000).encode()
# A chain of 1,000 `$ref`s applied at each of 128 levels overflows the worker's stack.
CRASHING_SCHEMA: JsonObject = {
    "$defs": {
        **{f"l{index}": {"$ref": f"#/$defs/l{index + 1}"} for index in range(999)},
        "l999": {"items": {"$ref": "#/$defs/l0"}},
    },
    "$ref": "#/$defs/l0",
}
CRASHING_BODY = b"[" * 128 + b"]" * 128
TIME_LIMIT = "the request body could not be validated within the time limit"
FAILED = "the request body could not be validated"
UNAVAILABLE = "request validation is unavailable"
CLOSING = "request validation is shutting down"
MISMATCH = "request body does not match the request schema"
LOGGER = "app.core.request_validation"


class OpenPool(Protocol):
    def __call__(
        self,
        *,
        workers: int = 2,
        timeout_seconds: float = DEADLINE_SECONDS,
        compile_timeout_seconds: float = COMPILE_DEADLINE_SECONDS,
    ) -> ProcessRequestValidationPool: ...


@pytest.fixture(params=["asyncio", "uvloop"])
def event_loop_policy(request: pytest.FixtureRequest) -> asyncio.AbstractEventLoopPolicy:
    if request.param == "uvloop":
        return uvloop.EventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
async def open_pool() -> AsyncIterator[OpenPool]:
    pools: list[ProcessRequestValidationPool] = []

    def open_pool(
        *,
        workers: int = 2,
        timeout_seconds: float = DEADLINE_SECONDS,
        compile_timeout_seconds: float = COMPILE_DEADLINE_SECONDS,
    ) -> ProcessRequestValidationPool:
        pool = ProcessRequestValidationPool(
            workers=workers,
            timeout_seconds=timeout_seconds,
            compile_timeout_seconds=compile_timeout_seconds,
        )
        pools.append(pool)
        return pool

    yield open_pool
    for pool in pools:
        await pool.close()


def _exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _address_space_limits(pid: int) -> list[str]:
    """A process's soft and hard address space limits, as Linux reports them."""
    limits = Path(f"/proc/{pid}/limits").read_text().splitlines()
    (address_space,) = [line for line in limits if line.startswith("Max address space")]
    return address_space.split()[3:5]


async def _worst_tick(stop: asyncio.Event) -> float:
    """The longest a 1 ms timer took to fire until `stop`: how long the loop stalled."""
    loop = asyncio.get_running_loop()
    worst = 0.0
    while not stop.is_set():
        start = loop.time()
        await asyncio.sleep(0.001)
        worst = max(worst, loop.time() - start)
    return worst


def _integers(size: int) -> bytes:
    """A JSON array of zeros exactly `size` bytes long."""
    return b"[" + b"0," * ((size - 4) // 2) + b"0] "


async def test_a_request_schema_that_compiles_is_accepted(open_pool: OpenPool) -> None:
    schema: JsonObject = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            },
        },
    }

    await check_request_schema_compiles(pool=open_pool(), schema=schema)


@pytest.mark.parametrize(
    ("schema", "refusal"),
    [
        pytest.param(
            {"properties": {"text": {"pattern": "(?=a)"}}},
            'request_schema pattern "(?=a)" is not supported: patterns must avoid lookaround '
            "and backreferences and compile within 10240 bytes at /properties/text/pattern",
            id="lookaround",
        ),
        # The class escapes compile in quadratic time: about 83 s for this 32 KiB schema.
        pytest.param(
            {"pattern": "\\s" * 10_900},
            "request_schema is too expensive to compile",
            id="quadratic_class_escapes",
        ),
        # Compiles in about 0.6 s, so it can never be stored: every validation of it would
        # overrun the validation deadline on a worker that had to compile it first.
        pytest.param(
            {"pattern": "(?i)" + "|".join(["[\u0100-\uffff]"] * 1_780)},
            "request_schema is too expensive to compile",
            id="case_folded_alternation",
        ),
    ],
)
async def test_a_request_schema_that_does_not_compile_in_time_is_refused_at_request_schema(
    open_pool: OpenPool,
    schema: JsonObject,
    refusal: str,
) -> None:
    pool = open_pool()

    with pytest.raises(InvalidInputError, match=f"^{re.escape(refusal)}$") as refused:
        await check_request_schema_compiles(pool=pool, schema=schema)

    assert refused.value.extensions == {
        "errors": [
            {
                "type": "value_error",
                "loc": ["body", "request_schema"],
                "msg": f"Value error, {refusal}",
            },
        ],
    }
    await check_request_schema_compiles(pool=pool, schema={"type": "object"})


@pytest.mark.parametrize(
    ("schema", "body"),
    [
        pytest.param(
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            b'{"text": "hello", "extra": [1, 2.5, null, true, {"k": "v"}]}',
            id="object",
        ),
        pytest.param({"items": {"type": "integer"}}, _integers(300 * 1024), id="300_kib"),
        pytest.param(
            {"items": {"type": "integer"}}, _integers(REQUEST_BODY_MAX_BYTES), id="largest"
        ),
    ],
)
async def test_a_body_matching_the_schema_is_accepted_without_stalling_the_event_loop(
    open_pool: OpenPool,
    schema: JsonObject,
    body: bytes,
) -> None:
    pool = open_pool()
    await validate_request_body(pool=pool, schema={}, body=b"{}")  # started
    stop = asyncio.Event()
    ticker = asyncio.create_task(_worst_tick(stop))

    try:
        await validate_request_body(pool=pool, schema=schema, body=body)
    finally:
        stop.set()

    assert await ticker < 0.1


@pytest.mark.parametrize(
    ("schema", "body", "message"),
    [
        pytest.param(
            {"properties": {"a/b": {"items": {"type": "integer"}}}},
            b'{"a/b": [1, "x"]}',
            f'{MISMATCH}: "x" is not of type "integer" at /a~1b/1',
            id="first_error_located",
        ),
        pytest.param({}, b'{"a": ', "request body is not valid JSON", id="malformed"),
        pytest.param(
            {"minimum": 0},
            b"1e400",
            "request body holds a number that is not finite",
            id="beyond_a_double",
        ),
    ],
)
async def test_a_body_is_refused_naming_the_problem(
    open_pool: OpenPool,
    schema: JsonObject,
    body: bytes,
    message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=f"^{re.escape(message)}$") as refused:
        await validate_request_body(pool=open_pool(), schema=schema, body=body)

    assert refused.value.problem_type is None


async def test_an_oversized_body_is_refused_before_any_worker_starts(open_pool: OpenPool) -> None:
    pool = open_pool()

    with pytest.raises(
        InvalidInputError,
        match=f"^request body must be at most {REQUEST_BODY_MAX_BYTES} bytes$",
    ):
        await validate_request_body(pool=pool, schema={}, body=b" " * (REQUEST_BODY_MAX_BYTES + 1))

    assert pool.pids == ()


async def test_a_validation_past_the_deadline_is_refused_and_its_worker_replaced(
    open_pool: OpenPool,
) -> None:
    pool = open_pool(workers=1, timeout_seconds=SHORT_DEADLINE_SECONDS)
    await validate_request_body(pool=pool, schema={}, body=b"{}")
    (overrunning,) = pool.pids

    with pytest.raises(InvalidInputError, match=f"^{TIME_LIMIT}$") as refused:
        await validate_request_body(pool=pool, schema=SLOW_SCHEMA, body=SLOW_BODY)

    assert refused.value.problem_type == "request_validation_timeout"
    await validate_request_body(pool=pool, schema={"type": "object"}, body=b"{}")
    (replacement,) = pool.pids
    assert replacement != overrunning
    assert not _exists(overrunning)


async def test_a_body_that_ends_its_worker_is_refused_and_the_worker_replaced(
    open_pool: OpenPool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = open_pool(workers=1)
    await validate_request_body(pool=pool, schema={}, body=b"{}")
    (crashing,) = pool.pids

    with pytest.raises(
        InvalidInputError,
        match=f"^{re.escape(FAILED)}$",
    ) as refused:
        await validate_request_body(pool=pool, schema=CRASHING_SCHEMA, body=CRASHING_BODY)

    assert refused.value.problem_type == "request_validation_failed"
    (record,) = [record for record in caplog.records if record.name == LOGGER]
    assert (record.levelname, record.getMessage()) == (
        "WARNING",
        "request validation worker exited",
    )
    assert vars(record)["exitcode"] < 0  # ended by a signal: SIGSEGV or SIGBUS
    await validate_request_body(pool=pool, schema={"type": "object"}, body=b"{}")
    assert pool.pids != (crashing,)


async def test_a_worker_killed_while_idle_is_replaced_without_failing_the_next_call(
    open_pool: OpenPool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = open_pool(workers=1)
    await validate_request_body(pool=pool, schema={}, body=b"{}")
    (killed,) = pool.pids
    os.kill(killed, 9)
    for _ in range(500):
        if not _exists(killed):
            break
        await asyncio.sleep(0.01)

    await validate_request_body(pool=pool, schema={"type": "object"}, body=b"{}")

    (replacement,) = pool.pids
    assert replacement != killed
    assert [record for record in caplog.records if record.name == LOGGER] == []


async def test_workers_that_cannot_start_make_the_pool_unavailable(
    open_pool: OpenPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A worker that exits before it is ready, as one would that cannot import the app.
    monkeypatch.setattr(request_validation, "_WORKER_COMMAND", (sys.executable, "-c", "pass"))
    pool = open_pool()

    with pytest.raises(UnavailableError, match=f"^{re.escape(UNAVAILABLE)}$"):
        await validate_request_body(pool=pool, schema={}, body=b"{}")

    (record,) = [record for record in caplog.records if record.name == LOGGER]
    assert (record.levelname, record.getMessage()) == (
        "ERROR",
        "request validation workers cannot start or read requests",
    )
    assert pool.pids == ()


@pytest.mark.parametrize(
    ("workers", "expected"),
    [
        pytest.param(2, [InvalidInputError, InvalidInputError], id="each_on_its_own_worker"),
        # The first holds the only worker while it starts and then for the deadline; the
        # second gives up waiting for it after the deadline.
        pytest.param(1, [InvalidInputError, UnavailableError], id="the_second_waits_the_deadline"),
    ],
)
async def test_validations_run_in_parallel_up_to_the_number_of_workers(
    open_pool: OpenPool,
    workers: int,
    expected: list[type[Exception]],
) -> None:
    pool = open_pool(workers=workers, timeout_seconds=SHORT_DEADLINE_SECONDS)

    results = await asyncio.gather(
        validate_request_body(pool=pool, schema=SLOW_SCHEMA, body=SLOW_BODY),
        validate_request_body(pool=pool, schema=SLOW_SCHEMA, body=SLOW_BODY),
        return_exceptions=True,
    )

    assert [type(result) for result in results] == expected
    for result in results:
        if isinstance(result, UnavailableError):
            assert str(result) == "request validation is busy; retry shortly"
            assert result.headers == {"Retry-After": "1"}
        else:
            assert str(result) == TIME_LIMIT


async def test_closing_the_pool_fails_running_and_waiting_calls_at_once(
    open_pool: OpenPool,
) -> None:
    pool = open_pool(workers=1)
    await validate_request_body(pool=pool, schema={}, body=b"{}")
    running = asyncio.create_task(
        validate_request_body(pool=pool, schema=SLOW_SCHEMA, body=SLOW_BODY)
    )
    waiting = asyncio.create_task(validate_request_body(pool=pool, schema={}, body=b"{}"))
    await asyncio.sleep(0.1)  # the first is running, the second waiting for its worker

    await pool.close()

    # At once: they would otherwise wait out the 5 s deadline and fail differently.
    for call in (running, waiting):
        with pytest.raises(UnavailableError, match=f"^{re.escape(CLOSING)}$"):
            await call
    with pytest.raises(UnavailableError, match=f"^{re.escape(CLOSING)}$"):
        await validate_request_body(pool=pool, schema={}, body=b"{}")
    assert pool.pids == ()


async def test_the_resources_start_the_workers_on_first_use_and_stop_them_on_exit() -> None:
    async with open_resources(Settings(request_validation_workers=3)) as resources:
        pool = resources.request_validation_pool
        assert pool.pids == ()
        await asyncio.gather(
            *(validate_request_body(pool=pool, schema={}, body=b"{}") for _ in range(3))
        )
        pids = pool.pids
        assert len(pids) == 3

    assert not any(_exists(pid) for pid in pids)


@pytest.mark.skipif(sys.platform != "linux", reason="macOS does not enforce RLIMIT_AS")
async def test_a_worker_is_limited_in_address_space_on_linux(open_pool: OpenPool) -> None:
    pool = open_pool(workers=1)
    await validate_request_body(pool=pool, schema={}, body=b"{}")
    (pid,) = pool.pids

    assert _address_space_limits(pid) == [str(MEMORY_LIMIT_BYTES)] * 2
