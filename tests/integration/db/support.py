import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NamedTuple

from alembic.config import Config
from asyncpg.exceptions import ObjectInUseError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

import app.db.models  # noqa: F401
from app.core.config import Settings
from app.db.base import Base

TEST_DATABASE_SUFFIX = "_test"
MIGRATION_DATABASE_SUFFIX = "_migrations"
ALEMBIC_VERSION_TABLE = "alembic_version"
DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
RUN_ID_PATTERN = re.compile(r"[^A-Za-z0-9_]+")


class MigrationDatabase(NamedTuple):
    """A database reserved for tests that drive the migration chain by hand."""

    config: Config
    engine: AsyncEngine


class PostgresUnavailableError(RuntimeError):
    """Raised when the admin Postgres database cannot be reached."""


def get_test_database_url(database_url: str | None = None, *, suffix: str = "") -> str:
    resolved_database_url = database_url or Settings().database_url
    url = make_url(resolved_database_url)
    database_name = url.database
    if database_name is None:
        msg = "database URL is missing a database name"
        raise RuntimeError(msg)

    run_id = _build_test_run_id()

    return url.set(
        database=f"{database_name}{TEST_DATABASE_SUFFIX}_{run_id}{suffix}",
    ).render_as_string(
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
        # Only a failure to connect means PostgreSQL is unavailable. A statement that
        # fails on the open connection (a DROP at teardown) raises as it is.
        try:
            connection = await engine.connect()
        except (OSError, OperationalError, DBAPIError) as exc:
            msg = "PostgreSQL is unavailable for DB-backed tests"
            raise PostgresUnavailableError(msg) from exc
        try:
            yield connection
        finally:
            await connection.close()


async def recreate_test_database(database_url: str) -> None:
    database_name = get_database_name(database_url)

    async with admin_connection(database_url) as connection:
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        await connection.execute(text(f'CREATE DATABASE "{database_name}"'))


async def truncate_all_tables(engine: AsyncEngine) -> None:
    require_test_database_url(engine.url.render_as_string(hide_password=False))

    # CASCADE makes ordering irrelevant, and sorting would warn about the
    # services <-> service_revisions foreign key cycle on every call.
    table_names = [name for name in Base.metadata.tables if name != ALEMBIC_VERSION_TABLE]
    if not table_names:
        return

    targets = ", ".join(f'"{table_name}"' for table_name in table_names)
    async with engine.begin() as connection:
        # A connection leaked by an earlier test still holds locks on these tables,
        # and TRUNCATE takes ACCESS EXCLUSIVE: time out so the leak fails loudly
        # here instead of hanging the whole run.
        await connection.execute(text("SET lock_timeout = '5s'"))
        await connection.execute(text(f"TRUNCATE {targets} RESTART IDENTITY CASCADE"))


async def drop_test_database(database_url: str) -> None:
    database_name = get_database_name(database_url)

    async with admin_connection(database_url) as connection:
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # The process exists but belongs to another user.
    return True


async def drop_stale_test_databases(database_url: str) -> None:
    """Drop test databases left behind by earlier runs that were killed before teardown.

    A killed run (SIGTERM from a timeout, SIGKILL, a crashed xdist worker) never reaches
    the teardown that drops its databases, and the next run never reuses them because
    their names end in the creating process's id. A database is dropped only when its
    name is one this harness creates for `database_url`, no process with the id in that
    name exists on this host, and nobody is connected to it. A database whose DROP still
    finds it in use is kept. If an unrelated process has since taken the id, the database
    stays until a later run finds the id free.
    """
    harness_name = re.compile(
        rf"{re.escape(get_database_name(database_url))}{re.escape(TEST_DATABASE_SUFFIX)}"
        rf"_(?:local|gw\d+)_(?P<pid>\d+)(?:{re.escape(MIGRATION_DATABASE_SUFFIX)})?",
    )
    async with admin_connection(database_url) as connection:
        unused_names = await connection.scalars(
            text(
                "SELECT datname FROM pg_database AS d WHERE NOT EXISTS "
                "(SELECT 1 FROM pg_stat_activity AS a WHERE a.datname = d.datname)",
            ),
        )
        for name in unused_names.all():
            match = harness_name.fullmatch(name)
            if match is None or _is_running(int(match["pid"])):
                continue
            try:
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            except DBAPIError as exc:
                # Someone connected after the SELECT above: the database is in use after all.
                if not isinstance(getattr(exc.orig, "__cause__", None), ObjectInUseError):
                    raise
