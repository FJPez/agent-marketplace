import asyncio

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine.interfaces import ReflectedIndex
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration.db.support import MigrationDatabase

ALEMBIC_VERSION_TABLE = "alembic_version"
DOMAIN_TABLES = {
    "accounts",
    "api_keys",
    "endpoint_prices",
    "moderation_actions",
    "provider_upstreams",
    "service_endpoints",
    "service_health_checks",
    "service_revisions",
    "service_tags",
    "services",
    "wallet_change_log",
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


async def get_index_specs(
    db_engine: AsyncEngine,
    table_name: str,
) -> dict[str, ReflectedIndex]:
    async with db_engine.connect() as connection:
        indexes = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes(table_name),
        )
    return {name: index for index in indexes if (name := index["name"]) is not None}


def test_head_migration_creates_exactly_the_domain_tables(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    table_names = asyncio.run(get_table_names(db_engine))

    assert table_names - {ALEMBIC_VERSION_TABLE} == DOMAIN_TABLES


def test_head_migration_expands_accounts_table(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    columns = asyncio.run(get_column_specs(db_engine, "accounts"))

    assert {
        "id",
        "wallet_address",
        "account_type",
        "is_admin",
        "display_name",
        "nonce",
        "nonce_issued_at",
        "token_version",
        "wallet_changed_at",
        "pending_wallet_address",
        "created_at",
        "updated_at",
    }.issubset(columns)
    assert columns["wallet_address"]["nullable"] is True
    assert columns["display_name"]["nullable"] is False
    assert columns["token_version"]["nullable"] is False


def test_head_migration_points_service_provider_fk_at_accounts(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    foreign_keys = asyncio.run(get_foreign_key_specs(db_engine, "services"))
    provider_fk = next(
        fk for fk in foreign_keys if fk["constrained_columns"] == ["provider_account_id"]
    )

    assert provider_fk["referred_table"] == "accounts"
    assert provider_fk["referred_columns"] == ["id"]


def test_head_migration_cascades_moderation_actions_from_services(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    foreign_keys = asyncio.run(get_foreign_key_specs(db_engine, "moderation_actions"))
    service_fk = next(fk for fk in foreign_keys if fk["constrained_columns"] == ["service_id"])

    assert service_fk["referred_table"] == "services"
    assert service_fk["options"] == {"ondelete": "CASCADE"}


def test_head_migration_indexes_moderation_actions_by_latest_action(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    indexes = asyncio.run(get_index_specs(db_engine, "moderation_actions"))

    assert "ix_moderation_actions_service_id_id_desc" in indexes


async def _seed_service(db_engine: AsyncEngine, *, slug: str) -> int:
    async with db_engine.begin() as connection:
        account_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO accounts (display_name, wallet_address)
                    VALUES ('Migration Provider', '0x0000000000000000000000000000000000000019')
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        return (
            await connection.execute(
                text(
                    """
                    INSERT INTO services (provider_account_id, slug, name, summary, lifecycle)
                    VALUES (
                        :provider_account_id,
                        :slug,
                        'Migration Check Service',
                        'Migration check summary',
                        'draft'
                    )
                    RETURNING id
                    """
                ),
                {"provider_account_id": account_id, "slug": slug},
            )
        ).scalar_one()


async def _insert_health_check(db_engine: AsyncEngine, *, service_id: int) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO service_health_checks (service_id, check_name, status)
                VALUES (:service_id, 'publish-readiness', 'pass')
                """
            ),
            {"service_id": service_id},
        )


async def _delete_service(db_engine: AsyncEngine, *, service_id: int) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM services WHERE id = :service_id"),
            {"service_id": service_id},
        )


async def _read_health_check_service_ids(db_engine: AsyncEngine) -> list[int]:
    async with db_engine.connect() as connection:
        result = await connection.execute(
            text("SELECT service_id FROM service_health_checks ORDER BY service_id")
        )
        return [row[0] for row in result]


def test_head_migration_rejects_health_check_for_unknown_service(
    clean_database: None,
    db_engine: AsyncEngine,
) -> None:
    with pytest.raises(IntegrityError):
        asyncio.run(_insert_health_check(db_engine, service_id=987654))


def test_head_migration_cascades_health_checks_when_service_is_deleted(
    clean_database: None,
    db_engine: AsyncEngine,
) -> None:
    service_id = asyncio.run(_seed_service(db_engine, slug="cascade-health"))
    asyncio.run(_insert_health_check(db_engine, service_id=service_id))
    assert asyncio.run(_read_health_check_service_ids(db_engine)) == [service_id]

    asyncio.run(_delete_service(db_engine, service_id=service_id))

    assert asyncio.run(_read_health_check_service_ids(db_engine)) == []


def test_baseline_migration_downgrades_cleanly_with_catalogue_rows(
    migration_database: MigrationDatabase,
) -> None:
    config = migration_database.config
    engine = migration_database.engine
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    service_id = asyncio.run(_seed_service(engine, slug="downgrade-check"))
    asyncio.run(_insert_health_check(engine, service_id=service_id))

    try:
        command.downgrade(config, "base")

        assert asyncio.run(get_table_names(engine)) <= {ALEMBIC_VERSION_TABLE}
    finally:
        command.upgrade(config, "head")
