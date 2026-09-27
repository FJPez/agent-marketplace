"""Tests for the PostgreSQL test harness itself (tests/conftest.py and support.py)."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool
from tests.integration.db.support import (
    admin_connection,
    drop_stale_test_databases,
    get_database_name,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:1/agent_marketplace"

CreateDatabase = Callable[[str], Awaitable[str]]


def _exited_process_id() -> int:
    process = subprocess.Popen([sys.executable, "-c", ""])
    process.wait()
    return process.pid


@contextmanager
def _running_process() -> Iterator[int]:
    """Yield the id of a process that runs until the block ends and has exited after it."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
    )
    try:
        yield process.pid
    finally:
        process.communicate()


@asynccontextmanager
async def _connect(database_url: str, name: str) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(
        make_url(database_url).set(database=name),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


async def _database_exists(database_url: str, name: str) -> bool:
    async with admin_connection(database_url) as connection:
        found = await connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": name},
        )
    return found is not None


@pytest.fixture
async def create_database(
    use_dedicated_test_database: None,
    base_database_url: str,
) -> AsyncIterator[CreateDatabase]:
    created: list[str] = []

    async def create(name_template: str) -> str:
        name = name_template.format(
            base=get_database_name(base_database_url),
            exited=_exited_process_id(),
            running=os.getppid(),
        )
        async with admin_connection(base_database_url) as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return name

    yield create
    async with admin_connection(base_database_url) as connection:
        for name in created:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.mark.parametrize(
    ("name_template", "dropped"),
    [
        pytest.param("{base}_test_local_{exited}", True, id="abandoned_local_run"),
        pytest.param("{base}_test_gw1_{exited}_migrations", True, id="abandoned_xdist_run"),
        pytest.param("{base}_test_gw1_{running}", False, id="running_process"),
        pytest.param("{base}_test_local_{exited}_backup", False, id="not_a_harness_name"),
    ],
)
async def test_drop_stale_test_databases_drops_only_abandoned_harness_databases(
    create_database: CreateDatabase,
    base_database_url: str,
    name_template: str,
    dropped: bool,
) -> None:
    name = await create_database(name_template)

    await drop_stale_test_databases(base_database_url)

    assert await _database_exists(base_database_url, name) is not dropped


async def test_drop_stale_test_databases_keeps_a_database_that_is_in_use(
    create_database: CreateDatabase,
    base_database_url: str,
) -> None:
    async with AsyncExitStack() as stack:
        # Until the connection is open, the process in the name runs, so no reclaim
        # (another run's, at its start) can drop the database first.
        with _running_process() as pid:
            name = await create_database(f"{{base}}_test_local_{pid}")
            await stack.enter_async_context(_connect(base_database_url, name))

        await drop_stale_test_databases(base_database_url)

    assert await _database_exists(base_database_url, name)


async def test_drop_stale_test_databases_skips_a_database_it_cannot_drop(
    create_database: CreateDatabase,
    base_database_url: str,
) -> None:
    # A disabled logical replication subscription makes DROP DATABASE fail with
    # ObjectInUse while nobody is connected, as the reclaim's DROP does when someone
    # connects after it has listed the unused databases.
    with _running_process() as pid:
        name = await create_database(f"{{base}}_test_local_{pid}")
        async with _connect(base_database_url, name) as connection:
            await connection.execute(
                text(
                    "CREATE SUBSCRIPTION blocks_drop CONNECTION 'dbname=unused' "
                    "PUBLICATION unused WITH (connect = false, slot_name = NONE)"
                )
            )
    try:
        await drop_stale_test_databases(base_database_url)

        assert await _database_exists(base_database_url, name)
    finally:
        async with _connect(base_database_url, name) as connection:
            await connection.execute(text("DROP SUBSCRIPTION blocks_drop"))


@pytest.mark.usefixtures("use_dedicated_test_database")
async def test_admin_connection_reports_statement_errors_as_they_are(
    base_database_url: str,
) -> None:
    # Only a failure to connect means PostgreSQL is unavailable; a failed DROP at
    # teardown must surface instead of being skipped as "unavailable".
    with pytest.raises(DBAPIError, match="division by zero"):
        async with admin_connection(base_database_url) as connection:
            await connection.execute(text("SELECT 1/0"))


@pytest.mark.parametrize(
    ("env_overrides", "returncode", "summary"),
    [
        pytest.param({}, 0, "1 skipped", id="skips_by_default"),
        pytest.param({"APP_TEST_REQUIRE_DATABASE": "1"}, 1, "1 error", id="fails_when_required"),
    ],
)
def test_db_backed_tests_without_postgres(
    env_overrides: dict[str, str],
    returncode: int,
    summary: str,
) -> None:
    env = {key: value for key, value in os.environ.items() if key != "APP_TEST_REQUIRE_DATABASE"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/db/test_session.py::test_session_factory_connects_to_postgres",
            "-p",
            "no:cacheprovider",
            "-q",
        ],
        cwd=PROJECT_ROOT,
        env=env | {"APP_DATABASE_URL": UNREACHABLE_DATABASE_URL} | env_overrides,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == returncode, result.stdout
    assert summary in result.stdout
    assert "PostgreSQL is unavailable for DB-backed tests" in result.stdout
