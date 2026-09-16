import asyncio
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration.db.support import MigrationDatabase

DOMAIN_TABLES = {
    "accounts",
    "api_keys",
    "endpoint_prices",
    "invocations",
    "ledger_entries",
    "moderation_actions",
    "payment_attempts",
    "payouts",
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


async def get_check_constraint_names(
    db_engine: AsyncEngine,
    table_name: str,
) -> set[str]:
    async with db_engine.connect() as connection:
        constraints = await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_check_constraints(table_name),
        )
    return {constraint["name"] for constraint in constraints}


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
    _ = migrated_database

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
    _ = migrated_database

    foreign_keys = asyncio.run(get_foreign_key_specs(db_engine, "moderation_actions"))
    service_fk = next(fk for fk in foreign_keys if fk["constrained_columns"] == ["service_id"])

    assert service_fk["referred_table"] == "services"
    assert service_fk["options"] == {"ondelete": "CASCADE"}


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


def test_head_migration_downgrades_cleanly_with_service_rows(
    migration_database: MigrationDatabase,
) -> None:
    config = migration_database.config
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    asyncio.run(_seed_head_state_for_downgrade(migration_database.engine))

    try:
        command.downgrade(config, "base")
    finally:
        command.upgrade(config, "head")


async def _seed_service_for_health_checks(db_engine: AsyncEngine, *, slug: str) -> int:
    async with db_engine.begin() as connection:
        account_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO accounts (display_name, wallet_address)
                    VALUES ('Health Provider', '0x0000000000000000000000000000000000000019')
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
                        'Health Check Service',
                        'Health check summary',
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
    _ = clean_database

    with pytest.raises(IntegrityError):
        asyncio.run(_insert_health_check(db_engine, service_id=987654))


def test_head_migration_cascades_health_checks_when_service_is_deleted(
    clean_database: None,
    db_engine: AsyncEngine,
) -> None:
    _ = clean_database

    service_id = asyncio.run(_seed_service_for_health_checks(db_engine, slug="cascade-health"))
    asyncio.run(_insert_health_check(db_engine, service_id=service_id))
    assert asyncio.run(_read_health_check_service_ids(db_engine)) == [service_id]

    asyncio.run(_delete_service(db_engine, service_id=service_id))

    assert asyncio.run(_read_health_check_service_ids(db_engine)) == []


def test_health_check_service_fk_migration_drops_orphan_rows(
    migration_database: MigrationDatabase,
) -> None:
    config = migration_database.config
    engine = migration_database.engine
    command.downgrade(config, "base")
    try:
        command.upgrade(config, "moderation_actions_0018")
        service_id = asyncio.run(_seed_service_for_health_checks(engine, slug="orphan-health"))
        asyncio.run(_insert_health_check(engine, service_id=service_id))
        asyncio.run(_insert_health_check(engine, service_id=987654))

        command.upgrade(config, "head")

        assert asyncio.run(_read_health_check_service_ids(engine)) == [service_id]
    finally:
        command.downgrade(config, "base")


def test_health_check_service_fk_migration_round_trips_at_head(
    migration_database: MigrationDatabase,
) -> None:
    config = migration_database.config
    engine = migration_database.engine
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    try:
        command.downgrade(config, "moderation_actions_0018")

        foreign_keys = asyncio.run(get_foreign_key_specs(engine, "service_health_checks"))
        assert foreign_keys == []

        command.upgrade(config, "head")

        foreign_keys = asyncio.run(get_foreign_key_specs(engine, "service_health_checks"))
        service_fk = next(fk for fk in foreign_keys if fk["constrained_columns"] == ["service_id"])
        assert service_fk["referred_table"] == "services"
        assert service_fk["options"] == {"ondelete": "CASCADE"}
    finally:
        command.downgrade(config, "base")


def test_schema_alignment_migration_round_trips_drifted_column_types(
    migration_database: MigrationDatabase,
) -> None:
    config = migration_database.config
    engine = migration_database.engine
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    try:
        invocation_columns = asyncio.run(get_column_specs(engine, "invocations"))
        quote_columns = asyncio.run(get_column_specs(engine, "quotes"))
        assert str(invocation_columns["response_payload"]["type"]) == "JSONB"
        assert str(quote_columns["pricing_type"]["type"]) == "VARCHAR(14)"

        command.downgrade(config, "service_health_0019")

        invocation_columns = asyncio.run(get_column_specs(engine, "invocations"))
        quote_columns = asyncio.run(get_column_specs(engine, "quotes"))
        assert str(invocation_columns["response_payload"]["type"]) == "JSON"
        assert str(quote_columns["pricing_type"]["type"]) == "VARCHAR(50)"
    finally:
        command.downgrade(config, "base")


async def _seed_legacy_pricing_state(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as connection:
        account_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO accounts (display_name, wallet_address)
                    VALUES ('Pricing Provider', '0x0000000000000000000000000000000000000017')
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        service_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO services (provider_account_id, slug, name, summary, lifecycle)
                    VALUES (
                        :provider_account_id,
                        'pricing-check',
                        'Pricing Check',
                        'Pricing summary',
                        'draft'
                    )
                    RETURNING id
                    """
                ),
                {"provider_account_id": account_id},
            )
        ).scalar_one()
        for key, access_mode in (("free-endpoint", "free"), ("paid-endpoint", "paid")):
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
                        timeout_seconds
                    )
                    VALUES (
                        :service_id,
                        :key,
                        :key,
                        :access_mode,
                        CAST('{}' AS jsonb),
                        CAST('{}' AS jsonb),
                        30
                    )
                    """
                ),
                {"service_id": service_id, "key": key, "access_mode": access_mode},
            )
        await connection.execute(
            text(
                """
                INSERT INTO pricing_models (endpoint_id, pricing_type, amount_minor, currency)
                SELECT id, 'free', NULL, NULL
                FROM service_endpoints
                WHERE key = 'free-endpoint'
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO pricing_models (endpoint_id, pricing_type, amount_minor, currency)
                SELECT id, 'fixed_per_call', 500, 'USD'
                FROM service_endpoints
                WHERE key = 'paid-endpoint'
                """
            )
        )


async def _read_endpoint_prices(db_engine: AsyncEngine) -> list[tuple[str, int, str]]:
    async with db_engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT se.key, ep.amount_minor, ep.currency
                FROM endpoint_prices ep
                JOIN service_endpoints se ON se.id = ep.endpoint_id
                ORDER BY se.key
                """
            )
        )
        return [(row[0], row[1], row[2]) for row in result]


async def _read_legacy_pricing_models(db_engine: AsyncEngine) -> list[tuple[str, str]]:
    async with db_engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT se.key, pm.pricing_type
                FROM pricing_models pm
                JOIN service_endpoints se ON se.id = pm.endpoint_id
                ORDER BY se.key
                """
            )
        )
        return [(row[0], row[1]) for row in result]


async def _insert_endpoint_price_without_currency(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO endpoint_prices (endpoint_id, amount_minor, currency)
                SELECT id, 500, NULL
                FROM service_endpoints
                WHERE key = 'free-endpoint'
                """
            )
        )


def test_endpoint_prices_migration_round_trips_legacy_pricing_rows(
    migration_database: MigrationDatabase,
) -> None:
    config = migration_database.config
    engine = migration_database.engine
    command.downgrade(config, "base")
    try:
        command.upgrade(config, "auth_wallet_binding_0016")
        asyncio.run(_seed_legacy_pricing_state(engine))

        command.upgrade(config, "head")

        table_names = asyncio.run(get_table_names(engine))
        assert "endpoint_prices" in table_names
        assert "pricing_models" not in table_names

        columns = asyncio.run(get_column_specs(engine, "endpoint_prices"))
        assert "pricing_type" not in columns
        assert columns["amount_minor"]["nullable"] is False
        assert columns["currency"]["nullable"] is False

        assert asyncio.run(_read_endpoint_prices(engine)) == [("paid-endpoint", 500, "USD")]

        with pytest.raises(IntegrityError):
            asyncio.run(_insert_endpoint_price_without_currency(engine))

        command.downgrade(config, "auth_wallet_binding_0016")

        table_names = asyncio.run(get_table_names(engine))
        assert "pricing_models" in table_names
        assert "endpoint_prices" not in table_names
        assert asyncio.run(_read_legacy_pricing_models(engine)) == [
            ("free-endpoint", "free"),
            ("paid-endpoint", "fixed_per_call"),
        ]

        command.upgrade(config, "head")
        assert asyncio.run(_read_endpoint_prices(engine)) == [("paid-endpoint", 500, "USD")]
    finally:
        command.downgrade(config, "base")


async def _seed_invocation_context(db_engine: AsyncEngine) -> tuple[int, int, int]:
    async with db_engine.begin() as connection:
        provider_account_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO accounts (display_name, wallet_address)
                    VALUES ('Lease Provider', '0x0000000000000000000000000000000000000021')
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        consumer_account_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO accounts (display_name, wallet_address)
                    VALUES ('Lease Consumer', '0x0000000000000000000000000000000000000022')
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        service_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO services (provider_account_id, slug, name, summary, lifecycle)
                    VALUES (
                        :provider_account_id,
                        'lease-service',
                        'Lease Service',
                        'Lease summary',
                        'active'
                    )
                    RETURNING id
                    """
                ),
                {"provider_account_id": provider_account_id},
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
                        timeout_seconds
                    )
                    VALUES (
                        :service_id,
                        'translate',
                        'Translate',
                        'free',
                        CAST('{}' AS jsonb),
                        CAST('{}' AS jsonb),
                        30
                    )
                    RETURNING id
                    """
                ),
                {"service_id": service_id},
            )
        ).scalar_one()
    return consumer_account_id, service_id, endpoint_id


async def _insert_invocation_with_lease(
    db_engine: AsyncEngine,
    *,
    consumer_account_id: int,
    service_id: int,
    endpoint_id: int,
    idempotency_key: str,
    status: str,
    in_progress_until: datetime | None,
) -> None:
    async with db_engine.begin() as connection:
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
                    in_progress_until
                )
                VALUES (
                    :consumer_account_id,
                    :service_id,
                    :endpoint_id,
                    'translate',
                    'free',
                    :idempotency_key,
                    :request_hash,
                    :status,
                    :in_progress_until
                )
                """
            ),
            {
                "consumer_account_id": consumer_account_id,
                "service_id": service_id,
                "endpoint_id": endpoint_id,
                "idempotency_key": idempotency_key,
                "request_hash": "a" * 64,
                "status": status,
                "in_progress_until": in_progress_until,
            },
        )


async def _read_invocation_leases(db_engine: AsyncEngine) -> list[tuple[str, str, datetime | None]]:
    async with db_engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT idempotency_key, status, in_progress_until
                FROM invocations
                ORDER BY idempotency_key
                """
            )
        )
        return [(row[0], row[1], row[2]) for row in result]


def test_head_migration_rejects_a_lease_on_a_terminal_invocation(
    clean_database: None,
    db_engine: AsyncEngine,
) -> None:
    _ = clean_database

    consumer_account_id, service_id, endpoint_id = asyncio.run(
        _seed_invocation_context(db_engine),
    )

    with pytest.raises(IntegrityError):
        asyncio.run(
            _insert_invocation_with_lease(
                db_engine,
                consumer_account_id=consumer_account_id,
                service_id=service_id,
                endpoint_id=endpoint_id,
                idempotency_key="terminal-with-lease",
                status="succeeded",
                in_progress_until=datetime(2030, 1, 1, tzinfo=UTC),
            )
        )


def test_head_migration_accepts_in_progress_invocations_with_and_without_a_lease(
    clean_database: None,
    db_engine: AsyncEngine,
) -> None:
    _ = clean_database

    consumer_account_id, service_id, endpoint_id = asyncio.run(
        _seed_invocation_context(db_engine),
    )
    leased_until = datetime(2030, 1, 1, tzinfo=UTC)

    asyncio.run(
        _insert_invocation_with_lease(
            db_engine,
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            idempotency_key="in-progress-leased",
            status="in_progress",
            in_progress_until=leased_until,
        )
    )
    asyncio.run(
        _insert_invocation_with_lease(
            db_engine,
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            idempotency_key="in-progress-unleased",
            status="in_progress",
            in_progress_until=None,
        )
    )

    assert asyncio.run(_read_invocation_leases(db_engine)) == [
        ("in-progress-leased", "in_progress", leased_until),
        ("in-progress-unleased", "in_progress", None),
    ]


def test_execution_lease_migration_round_trips_at_head(
    migration_database: MigrationDatabase,
) -> None:
    config = migration_database.config
    engine = migration_database.engine
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    try:
        columns = asyncio.run(get_column_specs(engine, "invocations"))
        constraints = asyncio.run(get_check_constraint_names(engine, "invocations"))
        assert "in_progress_until" in columns
        assert "ck_invocations_lease_only_in_progress" in constraints

        command.downgrade(config, "schema_alignment_0020")

        columns = asyncio.run(get_column_specs(engine, "invocations"))
        constraints = asyncio.run(get_check_constraint_names(engine, "invocations"))
        assert "in_progress_until" not in columns
        assert "ck_invocations_lease_only_in_progress" not in constraints

        command.upgrade(config, "head")

        columns = asyncio.run(get_column_specs(engine, "invocations"))
        constraints = asyncio.run(get_check_constraint_names(engine, "invocations"))
        assert "in_progress_until" in columns
        assert "ck_invocations_lease_only_in_progress" in constraints
    finally:
        command.downgrade(config, "base")
