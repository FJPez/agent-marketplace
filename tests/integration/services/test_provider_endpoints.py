import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_record,
    create_moderation_action_record,
    create_pricing_record,
    create_provider_account_record,
    create_service_record,
)

from app.core.config import Settings
from app.core.enums import AccessMode, AppEnv, PricingModelType, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.core.json_types import JsonObject
from app.db.models import PricingModel, ProviderUpstream, Service, ServiceEndpoint, ServiceRevision
from app.services.provider_endpoints import (
    create_endpoint,
    get_endpoint,
    update_endpoint,
    upsert_upstream,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]

REQUEST_SCHEMA: JsonObject = {"type": "object", "properties": {"text": {"type": "string"}}}
RESPONSE_SCHEMA: JsonObject = {"type": "object", "properties": {"result": {"type": "string"}}}


UPSTREAM_CONFIG: JsonObject = {"headers": {"x-api-key": "secret"}, "retries": 2}


def _upstream_settings() -> Settings:
    return Settings(env=AppEnv.TEST, jwt_secret_key="test-secret-key-with-32-bytes-123")


async def _create_draft_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str = "service",
) -> int:
    return await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        lifecycle=ServiceLifecycle.DRAFT,
    )


async def test_create_endpoint_persists_free_endpoint_with_free_pricing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        endpoint = await create_endpoint(
            session=session,
            account_id=account_id,
            service_id=service_id,
            key="free-ping",
            name="  Free Ping  ",
            summary="  A summary  ",
            description="  A description  ",
            access_mode=AccessMode.FREE,
            request_schema=REQUEST_SCHEMA,
            response_schema=RESPONSE_SCHEMA,
            timeout_seconds=30,
            is_enabled=True,
            pricing=None,
        )

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint.id)
        persisted_pricing = await session.get(PricingModel, endpoint.id)

    assert persisted_endpoint is not None
    assert persisted_endpoint.service_id == service_id
    assert persisted_endpoint.key == "free-ping"
    assert persisted_endpoint.name == "Free Ping"
    assert persisted_endpoint.summary == "A summary"
    assert persisted_endpoint.description == "A description"
    assert persisted_endpoint.access_mode is AccessMode.FREE
    assert persisted_endpoint.timeout_seconds == 30
    assert persisted_endpoint.is_enabled is True
    assert persisted_pricing is not None
    assert persisted_pricing.pricing_type is PricingModelType.FREE
    assert persisted_pricing.amount_minor is None
    assert persisted_pricing.currency is None


async def test_create_endpoint_persists_paid_endpoint_with_fixed_pricing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        endpoint = await create_endpoint(
            session=session,
            account_id=account_id,
            service_id=service_id,
            key="paid-call",
            name="Paid Call",
            summary=None,
            description=None,
            access_mode=AccessMode.PAID,
            request_schema=REQUEST_SCHEMA,
            response_schema=RESPONSE_SCHEMA,
            timeout_seconds=30,
            is_enabled=True,
            pricing={
                "pricing_type": PricingModelType.FIXED_PER_CALL,
                "amount_minor": 250,
                "currency": "USD",
            },
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(PricingModel, endpoint.id)

    assert persisted_pricing is not None
    assert persisted_pricing.pricing_type is PricingModelType.FIXED_PER_CALL
    assert persisted_pricing.amount_minor == 250
    assert persisted_pricing.currency == "USD"


async def test_create_endpoint_paid_without_pricing_creates_no_pricing_row(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        endpoint = await create_endpoint(
            session=session,
            account_id=account_id,
            service_id=service_id,
            key="paid-no-pricing",
            name="Paid No Pricing",
            summary=None,
            description=None,
            access_mode=AccessMode.PAID,
            request_schema=REQUEST_SCHEMA,
            response_schema=RESPONSE_SCHEMA,
            timeout_seconds=30,
            is_enabled=True,
            pricing=None,
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(PricingModel, endpoint.id)

    assert persisted_pricing is None


async def test_create_endpoint_returns_endpoint_with_loaded_relations(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        created = await create_endpoint(
            session=session,
            account_id=account_id,
            service_id=service_id,
            key="loaded-relations",
            name="Loaded Relations",
            summary=None,
            description=None,
            access_mode=AccessMode.PAID,
            request_schema=REQUEST_SCHEMA,
            response_schema=RESPONSE_SCHEMA,
            timeout_seconds=30,
            is_enabled=True,
            pricing=None,
        )

    assert created.key == "loaded-relations"
    assert created.pricing is None
    assert created.upstream is None


async def test_create_endpoint_rejects_duplicate_key_same_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        await create_endpoint(
            session=session,
            account_id=account_id,
            service_id=service_id,
            key="dup-key",
            name="Endpoint",
            summary=None,
            description=None,
            access_mode=AccessMode.FREE,
            request_schema=REQUEST_SCHEMA,
            response_schema=RESPONSE_SCHEMA,
            timeout_seconds=30,
            is_enabled=True,
            pricing=None,
        )

    async with db_session_factory() as session:
        with pytest.raises(ConflictError):
            await create_endpoint(
                session=session,
                account_id=account_id,
                service_id=service_id,
                key="dup-key",
                name="Endpoint 2",
                summary=None,
                description=None,
                access_mode=AccessMode.FREE,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
                pricing=None,
            )


async def test_create_endpoint_allows_same_key_different_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_a_id = await _create_draft_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="service-a",
    )
    service_b_id = await _create_draft_service(
        db_session_factory,
        provider_account_id=account_id,
        slug="service-b",
    )

    async with db_session_factory() as session:
        await create_endpoint(
            session=session,
            account_id=account_id,
            service_id=service_a_id,
            key="shared-key",
            name="Endpoint A",
            summary=None,
            description=None,
            access_mode=AccessMode.FREE,
            request_schema=REQUEST_SCHEMA,
            response_schema=RESPONSE_SCHEMA,
            timeout_seconds=30,
            is_enabled=True,
            pricing=None,
        )

    async with db_session_factory() as session:
        endpoint_b = await create_endpoint(
            session=session,
            account_id=account_id,
            service_id=service_b_id,
            key="shared-key",
            name="Endpoint B",
            summary=None,
            description=None,
            access_mode=AccessMode.FREE,
            request_schema=REQUEST_SCHEMA,
            response_schema=RESPONSE_SCHEMA,
            timeout_seconds=30,
            is_enabled=True,
            pricing=None,
        )

    assert endpoint_b.key == "shared-key"
    assert endpoint_b.service_id == service_b_id


@pytest.mark.parametrize(
    ("key", "name", "timeout_seconds", "access_mode", "pricing"),
    [
        ("Bad_Key", "Name", 30, AccessMode.FREE, None),
        ("valid-key", "  ", 30, AccessMode.FREE, None),
        ("valid-key", "Name", 0, AccessMode.FREE, None),
        ("valid-key", "Name", 3601, AccessMode.FREE, None),
        (
            "valid-key",
            "Name",
            30,
            AccessMode.FREE,
            {
                "pricing_type": PricingModelType.FIXED_PER_CALL,
                "amount_minor": 100,
                "currency": "USD",
            },
        ),
        ("valid-key", "Name", 30, AccessMode.PAID, {"pricing_type": PricingModelType.FREE}),
        (
            "valid-key",
            "Name",
            30,
            AccessMode.PAID,
            {
                "pricing_type": PricingModelType.FIXED_PER_CALL,
                "amount_minor": None,
                "currency": "USD",
            },
        ),
        (
            "valid-key",
            "Name",
            30,
            AccessMode.FREE,
            {"pricing_type": PricingModelType.FREE, "unexpected": "value"},
        ),
        (
            "valid-key",
            "Name",
            30,
            AccessMode.FREE,
            {"pricing_type": PricingModelType.FREE, "amount_minor": 250, "currency": None},
        ),
        (
            "valid-key",
            "Name",
            30,
            AccessMode.FREE,
            {"pricing_type": PricingModelType.FREE, "amount_minor": None, "currency": "USD"},
        ),
    ],
)
async def test_create_endpoint_rejects_invalid_input(
    db_session_factory: async_sessionmaker[AsyncSession],
    key: str,
    name: str,
    timeout_seconds: int,
    access_mode: AccessMode,
    pricing: dict[str, object] | None,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await create_endpoint(
                session=session,
                account_id=account_id,
                service_id=service_id,
                key=key,
                name=name,
                summary=None,
                description=None,
                access_mode=access_mode,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=timeout_seconds,
                is_enabled=True,
                pricing=pricing,
            )


async def test_create_endpoint_rejects_active_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError):
            await create_endpoint(
                session=session,
                account_id=account_id,
                service_id=service_id,
                key="new-endpoint",
                name="New Endpoint",
                summary=None,
                description=None,
                access_mode=AccessMode.FREE,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
                pricing=None,
            )


async def test_create_endpoint_rejects_other_accounts_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    other_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(
        db_session_factory,
        provider_account_id=other_account_id,
    )

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await create_endpoint(
                session=session,
                account_id=account_id,
                service_id=service_id,
                key="new-endpoint",
                name="New Endpoint",
                summary=None,
                description=None,
                access_mode=AccessMode.FREE,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
                pricing=None,
            )


async def test_get_endpoint_raises_not_found_for_missing_id(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await get_endpoint(session=session, account_id=account_id, endpoint_id=999_999)


async def test_get_endpoint_raises_not_found_for_other_accounts_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    other_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=other_account_id,
        slug="service",
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await get_endpoint(session=session, account_id=account_id, endpoint_id=endpoint_id)


async def test_update_endpoint_rejects_empty_updates(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                updates={},
            )


async def test_update_endpoint_rejects_unknown_field(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                updates={"key": "new-key"},
            )


@pytest.mark.parametrize(
    "field",
    ["name", "access_mode", "request_schema", "response_schema", "timeout_seconds", "is_enabled"],
)
async def test_update_endpoint_rejects_null_required_field(
    db_session_factory: async_sessionmaker[AsyncSession],
    field: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                updates={field: None},
            )


async def test_update_endpoint_clears_summary_and_description(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        summary="original summary",
        description="original description",
    )

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            updates={"summary": None, "description": None},
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted is not None
    assert persisted.summary is None
    assert persisted.description is None


async def test_update_endpoint_draft_persists_fields_and_bumps_updated_at(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        before = await session.get(ServiceEndpoint, endpoint_id)
        assert before is not None
        before_updated_at = before.updated_at

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            updates={"name": "  New Name  ", "summary": "  New Summary  ", "timeout_seconds": 45},
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted is not None
    assert persisted.name == "New Name"
    assert persisted.summary == "New Summary"
    assert persisted.timeout_seconds == 45
    assert persisted.updated_at > before_updated_at


async def test_update_endpoint_switches_paid_fixed_pricing_to_free(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    await create_pricing_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            updates={
                "access_mode": AccessMode.FREE,
                "pricing": {"pricing_type": PricingModelType.FREE},
            },
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(PricingModel, endpoint_id)

    assert persisted_pricing is not None
    assert persisted_pricing.endpoint_id == endpoint_id
    assert persisted_pricing.pricing_type is PricingModelType.FREE
    assert persisted_pricing.amount_minor is None
    assert persisted_pricing.currency is None


async def test_update_endpoint_paid_with_pricing_none_deletes_free_pricing_row(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    await create_pricing_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        pricing_type=PricingModelType.FREE,
        amount_minor=None,
        currency=None,
    )

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            updates={"pricing": None},
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(PricingModel, endpoint_id)

    assert persisted_pricing is None


async def test_update_endpoint_changes_fixed_pricing_amount(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    await create_pricing_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency="USD",
    )

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            updates={
                "pricing": {
                    "pricing_type": PricingModelType.FIXED_PER_CALL,
                    "amount_minor": 999,
                    "currency": "GBP",
                },
            },
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(PricingModel, endpoint_id)

    assert persisted_pricing is not None
    assert persisted_pricing.amount_minor == 999
    assert persisted_pricing.currency == "GBP"


async def test_update_endpoint_active_material_update_creates_one_revision(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        before = await session.get(Service, service_id)
        assert before is not None
        before_token = before.current_change_token

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            updates={"timeout_seconds": 60},
        )

    async with db_session_factory() as session:
        persisted_service = await session.get(Service, service_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )

    assert persisted_service is not None
    assert revision_count == 2
    assert persisted_service.current_change_token != before_token


async def test_update_endpoint_active_name_only_update_creates_zero_revisions(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            updates={"name": "Renamed Endpoint"},
        )

    async with db_session_factory() as session:
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )

    assert revision_count == 1


async def test_update_endpoint_active_paid_endpoint_without_pricing_rejects_material_update(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                updates={"timeout_seconds": 60},
            )


async def test_update_endpoint_rejects_active_paid_without_pricing_before_mutating_state(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
        timeout_seconds=30,
    )

    async with db_session_factory() as session:
        seeded_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        assert seeded_endpoint is not None
        seeded_updated_at = seeded_endpoint.updated_at

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                updates={"timeout_seconds": 60},
            )
        await session.commit()

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        persisted_pricing = await session.get(PricingModel, endpoint_id)

    assert persisted_endpoint is not None
    assert persisted_endpoint.timeout_seconds == 30
    assert persisted_endpoint.updated_at == seeded_updated_at
    assert persisted_pricing is None


async def test_update_endpoint_suspended_service_blocks_material_update(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)
    await create_moderation_action_record(
        db_session_factory,
        service_id=service_id,
        action="suspend",
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                updates={"timeout_seconds": 60},
            )


async def test_update_endpoint_suspended_service_allows_name_only_update(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)
    await create_moderation_action_record(
        db_session_factory,
        service_id=service_id,
        action="suspend",
    )

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            updates={"name": "Renamed While Suspended"},
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted is not None
    assert persisted.name == "Renamed While Suspended"


async def test_update_endpoint_rejects_other_accounts_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    other_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=other_account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                updates={"name": "New Name"},
            )


async def test_upsert_upstream_creates_row_for_draft_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )

    async with db_session_factory() as session:
        await upsert_upstream(
            session=session,
            settings=_upstream_settings(),
            account_id=account_id,
            endpoint_id=endpoint_id,
            base_url="http://127.0.0.1:9000",
            path="  /translate  ",
            http_method="POST",
            config=UPSTREAM_CONFIG,
        )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert persisted is not None
    assert persisted.base_url == "http://127.0.0.1:9000"
    assert persisted.path == "/translate"
    assert persisted.http_method == "POST"
    assert persisted.config == UPSTREAM_CONFIG


async def test_upsert_upstream_replaces_existing_row_in_place(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        await upsert_upstream(
            session=session,
            settings=_upstream_settings(),
            account_id=account_id,
            endpoint_id=endpoint_id,
            base_url="http://127.0.0.1:9000",
            path="/translate",
            http_method="POST",
            config=UPSTREAM_CONFIG,
        )

    async with db_session_factory() as session:
        first = await session.get(ProviderUpstream, endpoint_id)
        assert first is not None
        first_updated_at = first.updated_at

    async with db_session_factory() as session:
        await upsert_upstream(
            session=session,
            settings=_upstream_settings(),
            account_id=account_id,
            endpoint_id=endpoint_id,
            base_url="http://127.0.0.1:9100",
            path="/summarize",
            http_method="PUT",
            config={"headers": {}},
        )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)
        upstream_count = await session.scalar(
            select(func.count())
            .select_from(ProviderUpstream)
            .where(ProviderUpstream.endpoint_id == endpoint_id),
        )

    assert upstream_count == 1
    assert persisted is not None
    assert persisted.base_url == "http://127.0.0.1:9100"
    assert persisted.path == "/summarize"
    assert persisted.http_method == "PUT"
    assert persisted.config == {"headers": {}}
    assert persisted.updated_at > first_updated_at


async def test_upsert_upstream_rejects_active_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError):
            await upsert_upstream(
                session=session,
                settings=_upstream_settings(),
                account_id=account_id,
                endpoint_id=endpoint_id,
                base_url="http://127.0.0.1:9000",
                path="/translate",
                http_method="POST",
                config={},
            )


async def test_upsert_upstream_raises_not_found_for_missing_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await upsert_upstream(
                session=session,
                settings=_upstream_settings(),
                account_id=account_id,
                endpoint_id=999_999,
                base_url="http://127.0.0.1:9000",
                path="/translate",
                http_method="POST",
                config={},
            )


async def test_upsert_upstream_raises_not_found_for_other_accounts_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    other_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=other_account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await upsert_upstream(
                session=session,
                settings=_upstream_settings(),
                account_id=account_id,
                endpoint_id=endpoint_id,
                base_url="http://127.0.0.1:9000",
                path="/translate",
                http_method="POST",
                config={},
            )


async def test_upsert_upstream_rejects_unsafe_target(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match="upstream target is not allowed"):
            await upsert_upstream(
                session=session,
                settings=_upstream_settings(),
                account_id=account_id,
                endpoint_id=endpoint_id,
                base_url="https://127.0.0.1:9000",
                path="/translate",
                http_method="POST",
                config={},
            )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert persisted is None


@pytest.mark.parametrize(
    ("path", "http_method"),
    [("translate", "POST"), ("/translate", "post")],
)
async def test_upsert_upstream_rejects_invalid_path_or_method(
    db_session_factory: async_sessionmaker[AsyncSession],
    path: str,
    http_method: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await upsert_upstream(
                session=session,
                settings=_upstream_settings(),
                account_id=account_id,
                endpoint_id=endpoint_id,
                base_url="http://127.0.0.1:9000",
                path=path,
                http_method=http_method,
                config={},
            )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert persisted is None
