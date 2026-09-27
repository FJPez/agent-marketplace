import asyncio
import logging
import os
from collections.abc import AsyncIterator, Generator

# These must be set before any import of app.main, which creates the
# FastAPI application (and validates Settings) at module level.
os.environ.setdefault("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
os.environ.setdefault("APP_SIWE_DOMAIN", "testserver")
os.environ.setdefault("APP_ENV_FILE", ".env.test")

import coredis
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from tests.helpers.dns import TEST_UPSTREAM_ADDRESS, TEST_UPSTREAM_HOST, FakeResolver
from tests.integration.db.support import (
    MIGRATION_DATABASE_SUFFIX,
    MigrationDatabase,
    PostgresUnavailableError,
    drop_stale_test_databases,
    drop_test_database,
    get_test_database_url,
    recreate_test_database,
    require_test_database_url,
    truncate_all_tables,
)

from app.api.deps.dns import get_dns_resolver
from app.core.config import Settings, get_settings
from app.db.session import create_session_factory
from app.main import create_app

# settings first: the domain factories import its test constants, and a module
# imported before pytest registers it as a plugin cannot be assertion-rewritten.
pytest_plugins = (
    "tests.fixtures.settings",
    "tests.fixtures.domain",
)


@pytest.fixture(autouse=True)
def restore_logging_configuration() -> Generator[None, None, None]:
    """Undo, after each test, what `configure_logging` (run by every `create_app`) sets.

    Otherwise the `app` logger keeps a handler bound to the test's capture stream, which
    pytest closes when the test ends, and later tests log into a closed file.
    """
    app_logger = logging.getLogger("app")
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    handlers, filters, level = app_logger.handlers[:], app_logger.filters[:], app_logger.level
    uvicorn_error_filters = uvicorn_error_logger.filters[:]
    yield
    app_logger.handlers = handlers
    app_logger.filters = filters
    app_logger.setLevel(level)
    uvicorn_error_logger.filters = uvicorn_error_filters


def _build_alembic_config(database_url: str) -> Config:
    # Imported here, not at the top: pytest imports the `pytest_plugins` modules after
    # this module's body has run, and warns that a plugin module imported before then
    # cannot have its asserts rewritten.
    from tests.fixtures.settings import PROJECT_ROOT

    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    # The chain logs one INFO line per step, which is ~21k captured lines per run.
    logging.getLogger("alembic").setLevel(logging.WARNING)
    return config


@pytest.fixture(scope="session")
def base_database_url(base_test_env: None) -> str:
    return os.environ.get("APP_DATABASE_URL") or Settings().database_url


@pytest.fixture(scope="session")
def use_dedicated_test_database(
    base_test_env: None,
    base_database_url: str,
) -> Generator[None, None, None]:
    original_database_url = os.environ.get("APP_DATABASE_URL")
    test_database_url = get_test_database_url(base_database_url)
    get_settings.cache_clear()
    try:
        asyncio.run(drop_stale_test_databases(base_database_url))
        asyncio.run(recreate_test_database(test_database_url))
    except PostgresUnavailableError as exc:
        msg = f"{exc}. Start PostgreSQL to run DB-backed tests."
        # CI sets this so a missing PostgreSQL fails the run instead of skipping every
        # DB-backed test.
        if os.environ.get("APP_TEST_REQUIRE_DATABASE") == "1":
            raise pytest.fail.Exception(msg, pytrace=False) from exc
        raise pytest.skip.Exception(msg) from exc
    os.environ["APP_DATABASE_URL"] = test_database_url
    get_settings.cache_clear()

    try:
        yield
    finally:
        asyncio.run(drop_test_database(test_database_url))
        if original_database_url is None:
            os.environ.pop("APP_DATABASE_URL", None)
        else:
            os.environ["APP_DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def db_settings(use_dedicated_test_database: None) -> Settings:
    return Settings(database_url=require_test_database_url(Settings().database_url))


async def _flush_redis_database(redis_url: str) -> None:
    redis_client = coredis.Redis.from_url(redis_url, decode_responses=True)
    try:
        await redis_client.flushdb()
    finally:
        redis_client.connection_pool.disconnect()


# The database named by TEST_REDIS_URL is flushed, so it must be reserved for tests.
# Redis tests are pinned to one xdist worker via the "redis" xdist group and
# --dist loadgroup, so simultaneous independent test runs must not share the database.
@pytest.fixture
def test_redis_url() -> Generator[str, None, None]:
    redis_url = os.environ.get("TEST_REDIS_URL")
    assert redis_url is not None

    asyncio.run(_flush_redis_database(redis_url))
    try:
        yield redis_url
    finally:
        asyncio.run(_flush_redis_database(redis_url))


@pytest.fixture(scope="session")
def db_engine(db_settings: Settings) -> Generator[AsyncEngine, None, None]:
    # NullPool is SQLAlchemy's documented choice for an async engine shared across
    # event loops: pytest-asyncio gives each test its own loop, so a pooled asyncpg
    # connection would be handed to a later test still bound to a dead loop.
    engine = create_async_engine(db_settings.database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture
def db_session_factory(
    clean_database: None,
    db_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture(scope="session")
def alembic_config(db_settings: Settings) -> Config:
    return _build_alembic_config(db_settings.database_url)


@pytest.fixture(scope="session")
def migrated_database(alembic_config: Config) -> None:
    # The schema is built once; the database itself is dropped by
    # use_dedicated_test_database, so no downgrade is needed on teardown.
    command.upgrade(alembic_config, "head")


@pytest.fixture
def clean_database(migrated_database: None, db_engine: AsyncEngine) -> None:
    asyncio.run(truncate_all_tables(db_engine))


@pytest.fixture(scope="session")
def migration_database(
    use_dedicated_test_database: None,
    base_database_url: str,
) -> Generator[MigrationDatabase, None, None]:
    database_url = require_test_database_url(
        get_test_database_url(base_database_url, suffix=MIGRATION_DATABASE_SUFFIX),
    )
    asyncio.run(recreate_test_database(database_url))
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        yield MigrationDatabase(config=_build_alembic_config(database_url), engine=engine)
    finally:
        asyncio.run(engine.dispose())
        asyncio.run(drop_test_database(database_url))


@pytest.fixture
def dns_resolver() -> FakeResolver:
    """The resolver of every test: the test upstream host resolves to a public address."""
    return FakeResolver({TEST_UPSTREAM_HOST: [TEST_UPSTREAM_ADDRESS]})


@pytest.fixture
def app(
    use_dedicated_test_database: None,
    base_test_env: None,
    dns_resolver: FakeResolver,
) -> FastAPI:
    get_settings.cache_clear()
    application = create_app()
    # No API test resolves a real name.
    application.dependency_overrides[get_dns_resolver] = lambda: dns_resolver
    return application


@pytest.fixture
def client(
    app: FastAPI,
    clean_database: None,
) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
async def async_client(
    app: FastAPI,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as test_client:
            yield test_client
    get_settings.cache_clear()
