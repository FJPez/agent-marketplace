import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

DOMAIN_TABLES = {
    "accounts",
    "api_keys",
    "invocations",
    "ledger_entries",
    "moderation_actions",
    "payment_attempts",
    "payouts",
    "pricing_models",
    "provider_upstreams",
    "quotes",
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


def test_head_migration_creates_expected_unified_tables(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    _ = migrated_database

    table_names = asyncio.run(get_table_names(db_engine))

    assert DOMAIN_TABLES.issubset(table_names)
    assert "provider_profiles" not in table_names
    assert "consumer_profiles" not in table_names


def test_head_migration_expands_accounts_table(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    _ = migrated_database

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
    _ = migrated_database

    foreign_keys = asyncio.run(get_foreign_key_specs(db_engine, "services"))
    provider_fk = next(
        fk for fk in foreign_keys if fk["constrained_columns"] == ["provider_account_id"]
    )

    assert provider_fk["referred_table"] == "accounts"
    assert provider_fk["referred_columns"] == ["id"]


def test_head_migration_uses_bigint_for_payout_amount_minor(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    _ = migrated_database

    columns = asyncio.run(get_column_specs(db_engine, "payouts"))

    assert type(columns["amount_minor"]["type"]).__name__.upper() == "BIGINT"


async def _seed_head_state_for_downgrade(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as connection:
        result = await connection.execute(
            text(
                """
                INSERT INTO accounts (display_name, wallet_address)
                VALUES (:display_name, :wallet_address)
                RETURNING id
                """
            ),
            {
                "display_name": "Downgrade Provider",
                "wallet_address": "0x0000000000000000000000000000000000000042",
            },
        )
        account_id = result.scalar_one()
        await connection.execute(
            text(
                """
                INSERT INTO services (
                    provider_account_id,
                    slug,
                    name,
                    summary,
                    lifecycle
                )
                VALUES (
                    :provider_account_id,
                    :slug,
                    :name,
                    :summary,
                    :lifecycle
                )
                """
            ),
            {
                "provider_account_id": account_id,
                "slug": "downgrade-check",
                "name": "Downgrade Check",
                "summary": "Ensures legacy profile downgrade can restore service FKs.",
                "lifecycle": "draft",
            },
        )


def test_head_migration_downgrades_cleanly_with_service_rows(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_seed_head_state_for_downgrade(db_engine))

    try:
        command.downgrade(alembic_config, "base")
    finally:
        command.upgrade(alembic_config, "head")
