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


async def _insert_endpoint(
    db_engine: AsyncEngine,
    *,
    service_id: int,
    key: str,
    request_schema: str = "{}",
    response_schema: str = "{}",
    timeout_seconds: int = 30,
) -> int:
    async with db_engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    """
                    INSERT INTO service_endpoints (
                        service_id, key, name, access_mode,
                        request_schema, response_schema, timeout_seconds
                    )
                    VALUES (
                        :service_id, :key, 'Check Endpoint', 'free',
                        CAST(:request_schema AS jsonb), CAST(:response_schema AS jsonb),
                        :timeout_seconds
                    )
                    RETURNING id
                    """
                ),
                {
                    "service_id": service_id,
                    "key": key,
                    "request_schema": request_schema,
                    "response_schema": response_schema,
                    "timeout_seconds": timeout_seconds,
                },
            )
        ).scalar_one()


async def _insert_provider_upstream_config(
    db_engine: AsyncEngine,
    *,
    endpoint_id: int,
    config: str,
) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO provider_upstreams (endpoint_id, base_url, path, http_method, config)
                VALUES (
                    :endpoint_id, 'http://127.0.0.1:9000', '/invoke', 'POST', CAST(:config AS jsonb)
                )
                """
            ),
            {"endpoint_id": endpoint_id, "config": config},
        )


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


@pytest.mark.parametrize(
    ("request_schema", "response_schema", "constraint_name"),
    [
        ("[1, 2]", "{}", "ck_service_endpoints_request_schema_json_object"),
        ("{}", "[1, 2]", "ck_service_endpoints_response_schema_json_object"),
    ],
)
def test_head_migration_rejects_non_object_service_endpoint_schema(
    clean_database: None,
    db_engine: AsyncEngine,
    request_schema: str,
    response_schema: str,
    constraint_name: str,
) -> None:
    service_id = asyncio.run(_seed_service(db_engine, slug="endpoint-schema-check"))

    with pytest.raises(IntegrityError, match=constraint_name):
        asyncio.run(
            _insert_endpoint(
                db_engine,
                service_id=service_id,
                key="endpoint-schema-check",
                request_schema=request_schema,
                response_schema=response_schema,
            )
        )


@pytest.mark.parametrize("timeout_seconds", [0, 31])
def test_head_migration_rejects_endpoint_timeout_outside_the_cap(
    clean_database: None,
    db_engine: AsyncEngine,
    timeout_seconds: int,
) -> None:
    service_id = asyncio.run(_seed_service(db_engine, slug="timeout-cap-check"))

    with pytest.raises(IntegrityError, match="ck_service_endpoints_timeout_seconds_range"):
        asyncio.run(
            _insert_endpoint(
                db_engine,
                service_id=service_id,
                key="timeout-cap-check",
                timeout_seconds=timeout_seconds,
            )
        )


def test_head_migration_rejects_non_object_provider_upstream_config(
    clean_database: None,
    db_engine: AsyncEngine,
) -> None:
    service_id = asyncio.run(_seed_service(db_engine, slug="upstream-config-check"))
    endpoint_id = asyncio.run(
        _insert_endpoint(db_engine, service_id=service_id, key="upstream-config-check")
    )

    with pytest.raises(IntegrityError, match="ck_provider_upstreams_config_json_object"):
        asyncio.run(
            _insert_provider_upstream_config(db_engine, endpoint_id=endpoint_id, config="[1, 2]")
        )


def test_head_migration_rejects_non_object_service_revision_snapshot(
    clean_database: None,
    db_engine: AsyncEngine,
) -> None:
    service_id = asyncio.run(_seed_service(db_engine, slug="revision-snapshot-check"))

    with pytest.raises(IntegrityError, match="ck_service_revisions_snapshot_json_object"):
        asyncio.run(_insert_service_revision(db_engine, service_id=service_id, snapshot="[1, 2]"))


async def _insert_service_revision(
    db_engine: AsyncEngine,
    *,
    service_id: int,
    snapshot: str = "{}",
) -> int:
    async with db_engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    """
                    INSERT INTO service_revisions (
                        service_id, revision_number, change_token, snapshot
                    )
                    VALUES (:service_id, 1, :change_token, CAST(:snapshot AS jsonb))
                    RETURNING id
                    """
                ),
                {"service_id": service_id, "change_token": "c" * 64, "snapshot": snapshot},
            )
        ).scalar_one()


async def _set_current_revision(
    db_engine: AsyncEngine,
    *,
    service_id: int,
    revision_id: int,
) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(
            text("UPDATE services SET current_revision_id = :revision_id WHERE id = :service_id"),
            {"service_id": service_id, "revision_id": revision_id},
        )


def test_baseline_migration_downgrades_cleanly_with_catalogue_rows(
    migration_database: MigrationDatabase,
) -> None:
    config = migration_database.config
    engine = migration_database.engine
    command.upgrade(config, "head")
    service_id = asyncio.run(_seed_service(engine, slug="downgrade-check"))
    asyncio.run(_insert_health_check(engine, service_id=service_id))
    revision_id = asyncio.run(_insert_service_revision(engine, service_id=service_id))
    asyncio.run(_set_current_revision(engine, service_id=service_id, revision_id=revision_id))

    try:
        command.downgrade(config, "base")

        assert asyncio.run(get_table_names(engine)) <= {ALEMBIC_VERSION_TABLE}
    finally:
        command.upgrade(config, "head")


async def _read_endpoint_invocation_fields(
    db_engine: AsyncEngine,
    *,
    endpoint_id: int,
) -> tuple[int, bool, str]:
    async with db_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT timeout_seconds, supports_idempotency, response_content_type "
                    "FROM service_endpoints WHERE id = :endpoint_id"
                ),
                {"endpoint_id": endpoint_id},
            )
        ).one()
    return row.timeout_seconds, row.supports_idempotency, row.response_content_type


def test_endpoint_fields_migration_caps_existing_timeouts_and_fills_defaults(
    migration_database: MigrationDatabase,
) -> None:
    config = migration_database.config
    engine = migration_database.engine
    command.downgrade(config, "base")
    command.upgrade(config, "baseline_0001")
    try:
        service_id = asyncio.run(_seed_service(engine, slug="timeout-cap-upgrade"))
        endpoint_id = asyncio.run(
            _insert_endpoint(engine, service_id=service_id, key="slow", timeout_seconds=3600)
        )

        command.upgrade(config, "endpoint_fields_0002")

        assert asyncio.run(_read_endpoint_invocation_fields(engine, endpoint_id=endpoint_id)) == (
            30,
            False,
            "application/json",
        )
    finally:
        command.downgrade(config, "base")
        command.upgrade(config, "head")
