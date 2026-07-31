import asyncio
import os
from collections.abc import AsyncIterator, Generator
from contextlib import suppress
from pathlib import Path

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
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tests.integration.db.support import (
    PostgresUnavailableError,
    drop_test_database,
    get_test_database_url,
    recreate_test_database,
    require_test_database_url,
)

from app.core.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.main import create_app

pytest_plugins = (
    "tests.fixtures.domain",
    "tests.fixtures.settings",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def use_dedicated_test_database(base_test_env: None) -> Generator[None, None, None]:
    _ = base_test_env
    original_database_url = os.environ.get("APP_DATABASE_URL")
    base_database_url = original_database_url or Settings().database_url
    test_database_url = get_test_database_url(base_database_url)
    get_settings.cache_clear()
    try:
        asyncio.run(recreate_test_database(test_database_url))
    except PostgresUnavailableError as exc:
        raise pytest.skip.Exception(f"{exc}. Start PostgreSQL to run DB-backed tests.") from exc
    os.environ["APP_DATABASE_URL"] = test_database_url
    get_settings.cache_clear()

    try:
        yield
    finally:
        with suppress(PostgresUnavailableError):
            asyncio.run(drop_test_database(test_database_url))
        if original_database_url is None:
            os.environ.pop("APP_DATABASE_URL", None)
        else:
            os.environ["APP_DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def db_settings(use_dedicated_test_database: None) -> Settings:
    _ = use_dedicated_test_database
    return Settings(database_url=require_test_database_url(Settings().database_url))


async def _flush_redis_database(redis_url: str) -> None:
    redis_client = coredis.Redis.from_url(redis_url, decode_responses=True)
    try:
        await redis_client.flushdb()
    finally:
        redis_client.connection_pool.disconnect()


@pytest.fixture
def test_redis_url() -> Generator[str, None, None]:
    redis_url = os.environ.get("TEST_REDIS_URL")
    assert redis_url is not None

    asyncio.run(_flush_redis_database(redis_url))
    try:
        yield redis_url
    finally:
        asyncio.run(_flush_redis_database(redis_url))


@pytest.fixture
def db_engine(db_settings: Settings) -> Generator[AsyncEngine, None, None]:
    engine = create_engine(db_settings)
    try:
        yield engine
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture
def db_session_factory(
    db_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture
def alembic_config(db_settings: Settings) -> Config:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", db_settings.database_url)
    return config


@pytest.fixture
def migrated_database(alembic_config: Config) -> Generator[None, None, None]:
    command.upgrade(alembic_config, "head")
    try:
        yield
    finally:
        command.downgrade(alembic_config, "base")


@pytest.fixture
def app(use_dedicated_test_database: None, base_test_env: None) -> FastAPI:
    _ = use_dedicated_test_database
    _ = base_test_env
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(
    app: FastAPI,
    migrated_database: None,
) -> Generator[TestClient, None, None]:
    _ = migrated_database
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
async def async_client(
    app: FastAPI,
    migrated_database: None,
) -> AsyncIterator[AsyncClient]:
    _ = migrated_database
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as test_client:
            yield test_client
    get_settings.cache_clear()
