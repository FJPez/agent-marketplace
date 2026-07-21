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


def test_head_migration_adds_request_payout_columns(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    _ = migrated_database

    columns = asyncio.run(get_column_specs(db_engine, "payouts"))

    assert columns["destination_wallet"]["nullable"] is True
    assert {
        "request_idempotency_key",
        "failure_code",
        "prepared_raw_transaction",
        "chain_nonce",
    }.issubset(
        columns,
    )
    assert type(columns["chain_nonce"]["type"]).__name__.upper() == "BIGINT"


def test_head_migration_adds_payment_attempt_lifecycle_columns(
    migrated_database: None,
    db_engine: AsyncEngine,
) -> None:
    _ = migrated_database

    columns = asyncio.run(get_column_specs(db_engine, "payment_attempts"))

    assert {"status", "updated_at"}.issubset(columns)
    assert columns["status"]["nullable"] is False
    assert columns["updated_at"]["nullable"] is False


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


async def _seed_upstream_schema_invocation(db_engine: AsyncEngine) -> int:
    async with db_engine.begin() as connection:
        account_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO accounts (display_name, wallet_address)
                    VALUES (:display_name, :wallet_address)
                    RETURNING id
                    """
                ),
                {
                    "display_name": "Schema Downgrade Account",
                    "wallet_address": "0x0000000000000000000000000000000000000043",
                },
            )
        ).scalar_one()
        service_id = (
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
                        'active'
                    )
                    RETURNING id
                    """
                ),
                {
                    "provider_account_id": account_id,
                    "slug": "schema-downgrade-check",
                    "name": "Schema Downgrade Check",
                    "summary": "Ensures schema failures survive downgrade.",
                },
            )
        ).scalar_one()
        endpoint_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO service_endpoints (
                        service_id,
                        key,
                        name,
                        access_mode,
                        request_schema,
                        response_schema,
                        timeout_seconds,
                        is_enabled
                    )
                    VALUES (
                        :service_id,
                        'translate',
                        'Translate',
                        'free',
                        '{}'::jsonb,
                        '{}'::jsonb,
                        30,
                        true
                    )
                    RETURNING id
                    """
                ),
                {"service_id": service_id},
            )
        ).scalar_one()
        return (
            await connection.execute(
                text(
                    """
                    INSERT INTO invocations (
                        consumer_account_id,
                        service_id,
                        endpoint_id,
                        endpoint_key,
                        access_mode,
                        idempotency_key,
                        request_hash,
                        status,
                        upstream_status_code,
                        error_message,
                        failure_reason
                    )
                    VALUES (
                        :consumer_account_id,
                        :service_id,
                        :endpoint_id,
                        'translate',
                        'free',
                        'schema-downgrade-key',
                        :request_hash,
                        'failed',
                        200,
                        'response schema mismatch',
                        'upstream_schema'
                    )
                    RETURNING id
                    """
                ),
                {
                    "consumer_account_id": account_id,
                    "service_id": service_id,
                    "endpoint_id": endpoint_id,
                    "request_hash": "a" * 64,
                },
            )
        ).scalar_one()


async def _get_invocation_failure_reason(db_engine: AsyncEngine, invocation_id: int) -> str:
    async with db_engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT failure_reason FROM invocations WHERE id = :invocation_id"),
                {"invocation_id": invocation_id},
            )
        ).scalar_one()


def test_response_schema_failure_reason_downgrades_without_losing_invocation(
    alembic_config: Config,
    db_engine: AsyncEngine,
) -> None:
    command.upgrade(alembic_config, "head")
    invocation_id = asyncio.run(_seed_upstream_schema_invocation(db_engine))

    try:
        command.downgrade(alembic_config, "submission_hardening_0015")

        failure_reason = asyncio.run(_get_invocation_failure_reason(db_engine, invocation_id))

        assert failure_reason == "upstream_response"
    finally:
        command.upgrade(alembic_config, "head")


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
