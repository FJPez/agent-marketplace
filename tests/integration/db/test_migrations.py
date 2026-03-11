import asyncio

from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

DOMAIN_TABLES = {"accounts", "provider_profiles", "consumer_profiles"}
 

async def get_table_names(db_engine: AsyncEngine) -> set[str]:
    async with db_engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names()),
        )


def test_migrations_upgrade_creates_only_identity_tables(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    from alembic import command

    command.upgrade(alembic_config, "head")
    try:
        table_names = asyncio.run(get_table_names(db_engine))
    finally:
        command.downgrade(alembic_config, "base")

    assert DOMAIN_TABLES.issubset(table_names)
    assert table_names - {"alembic_version"} == DOMAIN_TABLES


def test_migrations_downgrade_removes_identity_tables(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    from alembic import command

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    table_names = asyncio.run(get_table_names(db_engine))

    assert DOMAIN_TABLES.isdisjoint(table_names)
