import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from tests.integration.db.support import require_test_database_url

from app.core.config import Settings
from app.db.session import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOMAIN_TABLES = {"accounts", "provider_profiles", "consumer_profiles"}


def get_alembic_config() -> Config:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        require_test_database_url(Settings().database_url),
    )
    return config


async def get_table_names() -> set[str]:
    settings = Settings(database_url=require_test_database_url(Settings().database_url))
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names()),
            )
    finally:
        await engine.dispose()


def test_migrations_upgrade_creates_only_identity_tables() -> None:
    config = get_alembic_config()

    command.upgrade(config, "head")
    try:
        table_names = asyncio.run(get_table_names())
    finally:
        command.downgrade(config, "base")

    assert DOMAIN_TABLES.issubset(table_names)
    assert table_names - {"alembic_version"} == DOMAIN_TABLES


def test_migrations_downgrade_removes_identity_tables() -> None:
    config = get_alembic_config()

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    table_names = asyncio.run(get_table_names())

    assert DOMAIN_TABLES.isdisjoint(table_names)
