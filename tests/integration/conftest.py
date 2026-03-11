import asyncio
import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tests.integration.db.support import (
    drop_test_database,
    get_test_database_url,
    recreate_test_database,
    require_test_database_url,
)

from app.core.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def use_dedicated_test_database() -> Generator[None, None, None]:
    original_database_url = os.environ.get("APP_DATABASE_URL")
    base_database_url = original_database_url or Settings().database_url
    test_database_url = get_test_database_url(base_database_url)
    get_settings.cache_clear()
    asyncio.run(recreate_test_database(test_database_url))
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
def db_settings() -> Settings:
    return Settings(database_url=require_test_database_url(Settings().database_url))


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
