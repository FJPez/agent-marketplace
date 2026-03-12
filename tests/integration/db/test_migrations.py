import asyncio

from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

DOMAIN_TABLES = {
    "accounts",
    "provider_profiles",
    "consumer_profiles",
    "provider_upstreams",
    "service_endpoints",
    "service_tags",
    "services",
}


async def get_table_names(db_engine: AsyncEngine) -> set[str]:
    async with db_engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names()),
        )


async def get_column_specs(
    db_engine: AsyncEngine,
    table_name: str,
) -> dict[str, dict[str, object]]:
    async with db_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns(table_name),
        )

    return {column["name"]: column for column in columns}


async def get_identity_column_specs(
    db_engine: AsyncEngine,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    async with db_engine.connect() as connection:
        provider_columns = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("provider_profiles"),
        )
        consumer_columns = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("consumer_profiles"),
        )

    return (
        {column["name"]: column for column in provider_columns},
        {column["name"]: column for column in consumer_columns},
    )


async def seed_identity_rows_at_revision_0001(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as connection:
        account_id = await connection.scalar(
            text("INSERT INTO accounts DEFAULT VALUES RETURNING id"),
        )
        if account_id is None:
            msg = "failed to seed account row"
            raise RuntimeError(msg)

        await connection.execute(
            text("INSERT INTO provider_profiles (account_id) VALUES (:account_id)"),
            {"account_id": account_id},
        )
        await connection.execute(
            text("INSERT INTO consumer_profiles (account_id) VALUES (:account_id)"),
            {"account_id": account_id},
        )


async def get_identity_profile_rows(
    db_engine: AsyncEngine,
    table_name: str,
) -> list[dict[str, object]]:
    async with db_engine.connect() as connection:
        result = await connection.execute(
            text(
                f'SELECT account_id, display_name FROM "{table_name}" ORDER BY account_id',
            ),
        )

    return [dict(row) for row in result.mappings().all()]


async def get_identity_upgrade_state(
    db_engine: AsyncEngine,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
]:
    async with db_engine.connect() as connection:
        provider_result = await connection.execute(
            text(
                'SELECT account_id, display_name FROM "provider_profiles" ORDER BY account_id',
            ),
        )
        consumer_result = await connection.execute(
            text(
                'SELECT account_id, display_name FROM "consumer_profiles" ORDER BY account_id',
            ),
        )
        provider_columns = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("provider_profiles"),
        )
        consumer_columns = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("consumer_profiles"),
        )

    return (
        [dict(row) for row in provider_result.mappings().all()],
        [dict(row) for row in consumer_result.mappings().all()],
        {column["name"]: column for column in provider_columns},
        {column["name"]: column for column in consumer_columns},
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


def test_migrations_upgrade_adds_non_nullable_display_name_columns(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    from alembic import command

    command.upgrade(alembic_config, "head")
    try:
        provider_columns, consumer_columns = asyncio.run(
            get_identity_column_specs(db_engine),
        )
    finally:
        command.downgrade(alembic_config, "base")

    assert provider_columns["display_name"]["nullable"] is False
    assert consumer_columns["display_name"]["nullable"] is False


def test_migrations_upgrade_creates_provider_service_tables(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    from alembic import command

    command.upgrade(alembic_config, "head")
    try:
        table_names = asyncio.run(get_table_names(db_engine))
    finally:
        command.downgrade(alembic_config, "base")

    assert {
        "services",
        "service_tags",
        "service_endpoints",
        "provider_upstreams",
    }.issubset(table_names)


def test_migrations_upgrade_backfills_display_name_for_existing_identity_rows(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    from alembic import command

    command.upgrade(alembic_config, "0001")
    try:
        asyncio.run(seed_identity_rows_at_revision_0001(db_engine))
        asyncio.run(db_engine.dispose())
        command.upgrade(alembic_config, "head")
        provider_rows, consumer_rows, provider_columns, consumer_columns = asyncio.run(
            get_identity_upgrade_state(db_engine),
        )
    finally:
        command.downgrade(alembic_config, "base")

    assert len(provider_rows) == 1
    assert provider_rows[0]["display_name"] == "Unknown Provider"
    assert len(consumer_rows) == 1
    assert consumer_rows[0]["display_name"] == "Unknown Consumer"
    assert provider_columns["display_name"]["nullable"] is False
    assert consumer_columns["display_name"]["nullable"] is False
