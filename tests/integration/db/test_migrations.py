import asyncio

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

DOMAIN_TABLES = {
    "accounts",
    "consumer_profiles",
    "moderation_actions",
    "pricing_models",
    "provider_profiles",
    "provider_upstreams",
    "service_endpoints",
    "service_health_checks",
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


async def get_foreign_key_specs(
    db_engine: AsyncEngine,
    table_name: str,
) -> list[dict[str, object]]:
    async with db_engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_foreign_keys(table_name),
        )


async def get_check_constraint_specs(
    db_engine: AsyncEngine,
    table_name: str,
) -> list[dict[str, object]]:
    async with db_engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_check_constraints(table_name),
        )


async def get_index_specs(
    db_engine: AsyncEngine,
    table_name: str,
) -> list[dict[str, object]]:
    async with db_engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes(table_name),
        )


async def get_moderation_table_specs(
    db_engine: AsyncEngine,
) -> tuple[
    dict[str, dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    async with db_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("moderation_actions"),
        )
        foreign_keys = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_foreign_keys("moderation_actions"),
        )
        check_constraints = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_check_constraints("moderation_actions"),
        )
        indexes = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("moderation_actions"),
        )

    return (
        {column["name"]: column for column in columns},
        foreign_keys,
        check_constraints,
        indexes,
    )


async def get_provider_service_table_specs(
    db_engine: AsyncEngine,
) -> tuple[set[str], list[dict[str, object]], list[dict[str, object]]]:
    table_names = await get_table_names(db_engine)
    service_constraints = await get_check_constraint_specs(db_engine, "services")
    endpoint_constraints = await get_check_constraint_specs(db_engine, "service_endpoints")
    return table_names, service_constraints, endpoint_constraints


async def get_service_health_table_specs(
    db_engine: AsyncEngine,
) -> tuple[
    dict[str, dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    async with db_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("service_health_checks"),
        )
        foreign_keys = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_foreign_keys(
                "service_health_checks",
            ),
        )
        check_constraints = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_check_constraints(
                "service_health_checks",
            ),
        )

    return {column["name"]: column for column in columns}, foreign_keys, check_constraints


async def get_pricing_table_specs(
    db_engine: AsyncEngine,
) -> tuple[
    dict[str, dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    async with db_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("pricing_models"),
        )
        foreign_keys = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_foreign_keys("pricing_models"),
        )
        check_constraints = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_check_constraints("pricing_models"),
        )

    return {column["name"]: column for column in columns}, foreign_keys, check_constraints


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
        table_names, service_constraints, endpoint_constraints = asyncio.run(
            get_provider_service_table_specs(db_engine),
        )
    finally:
        command.downgrade(alembic_config, "base")

    assert {
        "services",
        "service_tags",
        "service_endpoints",
        "provider_upstreams",
    }.issubset(table_names)
    assert any(
        all(
            token in str(constraint.get("sqltext", "")).lower()
            for token in ("lifecycle", "draft", "active", "suspended", "delisted")
        )
        for constraint in service_constraints
    )
    assert any(
        all(
            token in str(constraint.get("sqltext", "")).lower()
            for token in ("access_mode", "free", "paid")
        )
        for constraint in endpoint_constraints
    )


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


def test_migrations_upgrade_adds_moderation_actions_table(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    from alembic import command

    command.upgrade(alembic_config, "head")
    try:
        columns, foreign_keys, check_constraints, indexes = asyncio.run(
            get_moderation_table_specs(db_engine),
        )
    finally:
        command.downgrade(alembic_config, "base")

    assert columns.keys() >= {
        "id",
        "service_id",
        "actor_account_id",
        "action",
        "reason",
        "created_at",
    }
    assert columns["service_id"]["nullable"] is False
    assert columns["actor_account_id"]["nullable"] is True
    assert columns["action"]["nullable"] is False
    assert columns["reason"]["nullable"] is False
    assert any(
        fk["constrained_columns"] == ["actor_account_id"] and fk["referred_table"] == "accounts"
        for fk in foreign_keys
    )
    assert all(fk["referred_table"] != "services" for fk in foreign_keys)
    assert any(
        isinstance(constraint["sqltext"], str) and "suspend" in constraint["sqltext"]
        for constraint in check_constraints
    )
    assert any(index["column_names"] == ["service_id"] for index in indexes)


def test_moderation_migration_uses_branch_specific_revision_id(
    alembic_config: Config,
) -> None:
    script = ScriptDirectory.from_config(alembic_config)
    moderation_revision = script.get_revision("modadmin_20260312_153000")

    assert moderation_revision is not None
    assert moderation_revision.revision == "modadmin_20260312_153000"
    assert moderation_revision.path.endswith(
        "modadmin_20260312_153000_add_moderation_actions.py",
    )


def test_service_health_migration_uses_branch_specific_revision_id(
    alembic_config: Config,
) -> None:
    script = ScriptDirectory.from_config(alembic_config)
    health_revision = script.get_revision("service_health_0003")

    assert health_revision is not None
    assert health_revision.revision == "service_health_0003"
    assert health_revision.path.endswith(
        "service_health_0003_create_service_health_checks.py",
    )


def test_migrations_upgrade_creates_service_health_checks_without_service_foreign_key(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    from alembic import command

    command.upgrade(alembic_config, "head")
    try:
        columns, foreign_keys, check_constraints = asyncio.run(
            get_service_health_table_specs(db_engine),
        )
    finally:
        command.downgrade(alembic_config, "base")

    assert set(columns) == {
        "id",
        "service_id",
        "check_name",
        "status",
        "summary",
        "details",
        "checked_at",
    }
    assert columns["id"]["nullable"] is False
    assert columns["service_id"]["nullable"] is False
    assert columns["check_name"]["nullable"] is False
    assert columns["status"]["nullable"] is False
    assert columns["summary"]["nullable"] is True
    assert columns["details"]["nullable"] is True
    assert columns["checked_at"]["nullable"] is False
    assert foreign_keys == []
    matching_constraints = [
        constraint
        for constraint in check_constraints
        if all(
            token in str(constraint.get("sqltext", "")).lower()
            for token in ("status", "pass", "fail", "error")
        )
    ]
    assert matching_constraints


def test_migrations_upgrade_creates_pricing_models_table(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    from alembic import command

    command.upgrade(alembic_config, "head")
    try:
        columns, foreign_keys, check_constraints = asyncio.run(
            get_pricing_table_specs(db_engine),
        )
    finally:
        command.downgrade(alembic_config, "base")

    assert {"endpoint_id", "pricing_type", "amount_minor", "currency"}.issubset(columns)
    assert any(
        foreign_key["referred_table"] == "service_endpoints"
        and foreign_key["constrained_columns"] == ["endpoint_id"]
        for foreign_key in foreign_keys
    )
    assert any(
        "fixed_per_call" in str(constraint.get("sqltext")) for constraint in check_constraints
    )
