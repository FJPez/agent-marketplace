import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.core.config import Settings

TEST_DATABASE_SUFFIX = "_test"
DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
RUN_ID_PATTERN = re.compile(r"[^A-Za-z0-9_]+")


class PostgresUnavailableError(RuntimeError):
    """Raised when the admin Postgres database cannot be reached."""


def get_test_database_url(database_url: str | None = None) -> str:
    resolved_database_url = database_url or Settings().database_url
    url = make_url(resolved_database_url)
    database_name = url.database
    if database_name is None:
        msg = "database URL is missing a database name"
        raise RuntimeError(msg)

    run_id = _build_test_run_id()

    return url.set(database=f"{database_name}{TEST_DATABASE_SUFFIX}_{run_id}").render_as_string(
        hide_password=False,
    )


def get_admin_database_url(database_url: str | None = None) -> str:
    resolved_database_url = database_url or Settings().database_url
    return (
        make_url(resolved_database_url)
        .set(database="postgres")
        .render_as_string(
            hide_password=False,
        )
    )


def get_database_name(database_url: str) -> str:
    database_name = make_url(database_url).database
    if database_name is None:
        msg = "database URL is missing a database name"
        raise RuntimeError(msg)
    if DATABASE_NAME_PATTERN.fullmatch(database_name) is None:
        msg = "database name contains unsupported characters"
        raise RuntimeError(msg)
    return database_name


def require_test_database_url(database_url: str) -> str:
    database_name = get_database_name(database_url)
    if not (
        database_name.endswith(TEST_DATABASE_SUFFIX) or f"{TEST_DATABASE_SUFFIX}_" in database_name
    ):
        msg = "database integration tests must use a dedicated *_test database"
        raise RuntimeError(msg)
    return database_url


def _build_test_run_id() -> str:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "local")
    sanitized_worker_id = RUN_ID_PATTERN.sub("_", worker_id).strip("_") or "local"
    return f"{sanitized_worker_id}_{os.getpid()}"


@asynccontextmanager
async def admin_engine(database_url: str | None = None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        get_admin_database_url(database_url),
        isolation_level="AUTOCOMMIT",
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def admin_connection(database_url: str | None = None) -> AsyncIterator[AsyncConnection]:
    async with admin_engine(database_url) as engine:
        try:
            connection = await engine.connect()
        except (OSError, OperationalError, DBAPIError) as exc:
            msg = "PostgreSQL is unavailable for DB-backed tests"
            raise PostgresUnavailableError(msg) from exc

        async with connection:
            yield connection


async def recreate_test_database(database_url: str) -> None:
    database_name = get_database_name(database_url)

    async with admin_connection(database_url) as connection:
        await connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = :database_name "
                "AND pid <> pg_backend_pid()",
            ),
            {"database_name": database_name},
        )
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        await connection.execute(text(f'CREATE DATABASE "{database_name}"'))


async def drop_test_database(database_url: str) -> None:
    database_name = get_database_name(database_url)

    async with admin_connection(database_url) as connection:
        await connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = :database_name "
                "AND pid <> pg_backend_pid()",
            ),
            {"database_name": database_name},
        )
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
