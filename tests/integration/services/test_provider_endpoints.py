import pytest
from pydantic import HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_price_record,
    create_endpoint_record,
    create_moderation_action_record,
    create_provider_account_record,
    create_service_record,
    create_upstream_record,
)

from app.core.config import Settings
from app.core.enums import AccessMode, AppEnv, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.core.json_types import JsonObject
from app.db.models import EndpointPrice, ProviderUpstream, Service, ServiceEndpoint, ServiceRevision
from app.schemas.pricing import FixedPrice
from app.schemas.service import (
    EndpointCreateRequest,
    EndpointUpdateRequest,
    EndpointUpstreamRequest,
)
from app.services.provider_endpoints import (
    create_endpoint,
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


async def test_create_endpoint_free_creates_no_pricing_row(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        endpoint = await create_endpoint(
            session=session,
            account_id=account_id,
            service_id=service_id,
            request=EndpointCreateRequest(
                key="free-ping",
                name="  Free Ping  ",
                summary="  A summary  ",
                description="  A description  ",
                access_mode=AccessMode.FREE,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
            ),
        )

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint.id)
        persisted_pricing = await session.get(EndpointPrice, endpoint.id)

    assert persisted_endpoint is not None
    assert persisted_endpoint.service_id == service_id
    assert persisted_endpoint.key == "free-ping"
    assert persisted_endpoint.name == "Free Ping"
    assert persisted_endpoint.summary == "A summary"
    assert persisted_endpoint.description == "A description"
    assert persisted_endpoint.access_mode is AccessMode.FREE
    assert persisted_endpoint.timeout_seconds == 30
    assert persisted_endpoint.is_enabled is True
    assert persisted_pricing is None


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
            request=EndpointCreateRequest(
                key="paid-call",
                name="Paid Call",
                access_mode=AccessMode.PAID,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
                pricing=FixedPrice(amount_minor=250, currency="USD"),
            ),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint.id)

    assert persisted_pricing is not None
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
            request=EndpointCreateRequest(
                key="paid-no-pricing",
                name="Paid No Pricing",
                access_mode=AccessMode.PAID,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
            ),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint.id)

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
            request=EndpointCreateRequest(
                key="loaded-relations",
                name="Loaded Relations",
                access_mode=AccessMode.PAID,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
            ),
        )

    assert created.key == "loaded-relations"
    assert created.price is None
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
            request=EndpointCreateRequest(
                key="dup-key",
                name="Endpoint",
                access_mode=AccessMode.FREE,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
            ),
        )

    async with db_session_factory() as session:
        with pytest.raises(ConflictError):
            await create_endpoint(
                session=session,
                account_id=account_id,
                service_id=service_id,
                request=EndpointCreateRequest(
                    key="dup-key",
                    name="Endpoint 2",
                    access_mode=AccessMode.FREE,
                    request_schema=REQUEST_SCHEMA,
                    response_schema=RESPONSE_SCHEMA,
                    timeout_seconds=30,
                    is_enabled=True,
                ),
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
            request=EndpointCreateRequest(
                key="shared-key",
                name="Endpoint A",
                access_mode=AccessMode.FREE,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
            ),
        )

    async with db_session_factory() as session:
        endpoint_b = await create_endpoint(
            session=session,
            account_id=account_id,
            service_id=service_b_id,
            request=EndpointCreateRequest(
                key="shared-key",
                name="Endpoint B",
                access_mode=AccessMode.FREE,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
                is_enabled=True,
            ),
        )

    assert endpoint_b.key == "shared-key"
    assert endpoint_b.service_id == service_b_id


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
                request=EndpointCreateRequest(
                    key="new-endpoint",
                    name="New Endpoint",
                    access_mode=AccessMode.FREE,
                    request_schema=REQUEST_SCHEMA,
                    response_schema=RESPONSE_SCHEMA,
                    timeout_seconds=30,
                    is_enabled=True,
                ),
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
                request=EndpointCreateRequest(
                    key="new-endpoint",
                    name="New Endpoint",
                    access_mode=AccessMode.FREE,
                    request_schema=REQUEST_SCHEMA,
                    response_schema=RESPONSE_SCHEMA,
                    timeout_seconds=30,
                    is_enabled=True,
                ),
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
            changes=EndpointUpdateRequest(summary=None, description=None),
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
            changes=EndpointUpdateRequest(
                name="  New Name  ", summary="  New Summary  ", timeout_seconds=45
            ),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted is not None
    assert persisted.name == "New Name"
    assert persisted.summary == "New Summary"
    assert persisted.timeout_seconds == 45
    assert persisted.updated_at > before_updated_at


async def test_update_endpoint_draft_sets_price_on_unpriced_paid_endpoint(
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
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(pricing=FixedPrice(amount_minor=250, currency="USD")),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted_pricing is not None
    assert persisted_pricing.amount_minor == 250
    assert persisted_pricing.currency == "USD"


async def test_update_endpoint_draft_replaces_price_in_place(
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
    await create_endpoint_price_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency="USD",
    )

    async with db_session_factory() as session:
        seeded_pricing = await session.get(EndpointPrice, endpoint_id)
        assert seeded_pricing is not None
        seeded_updated_at = seeded_pricing.updated_at

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(pricing=FixedPrice(amount_minor=999, currency="GBP")),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)
        pricing_count = await session.scalar(
            select(func.count())
            .select_from(EndpointPrice)
            .where(EndpointPrice.endpoint_id == endpoint_id),
        )

    assert persisted_pricing is not None
    assert persisted_pricing.amount_minor == 999
    assert persisted_pricing.currency == "GBP"
    assert persisted_pricing.updated_at > seeded_updated_at
    assert pricing_count == 1


async def test_update_endpoint_draft_retains_price_when_pricing_is_omitted(
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
    await create_endpoint_price_record(
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
            changes=EndpointUpdateRequest(timeout_seconds=60),
        )

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted_endpoint is not None
    assert persisted_endpoint.timeout_seconds == 60
    assert persisted_pricing is not None
    assert persisted_pricing.amount_minor == 500
    assert persisted_pricing.currency == "USD"


async def test_update_endpoint_draft_clears_price_with_explicit_null(
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
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(pricing=None),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted_pricing is None


async def test_update_endpoint_draft_free_to_paid_without_price_creates_no_row(
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
        access_mode=AccessMode.FREE,
    )

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(access_mode=AccessMode.PAID),
        )

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted_endpoint is not None
    assert persisted_endpoint.access_mode is AccessMode.PAID
    assert persisted_pricing is None


async def test_update_endpoint_draft_free_to_paid_with_price_creates_row(
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
        access_mode=AccessMode.FREE,
    )

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(
                access_mode=AccessMode.PAID,
                pricing=FixedPrice(amount_minor=250, currency="USD"),
            ),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted_pricing is not None
    assert persisted_pricing.amount_minor == 250
    assert persisted_pricing.currency == "USD"


async def test_update_endpoint_draft_paid_to_free_deletes_existing_price(
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
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(access_mode=AccessMode.FREE),
        )

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted_endpoint is not None
    assert persisted_endpoint.access_mode is AccessMode.FREE
    assert persisted_pricing is None


async def test_update_endpoint_draft_rejects_price_on_free_endpoint(
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
        access_mode=AccessMode.FREE,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(pricing=FixedPrice(amount_minor=250, currency="USD")),
            )


async def test_update_endpoint_draft_free_endpoint_accepts_explicit_null_price(
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
        access_mode=AccessMode.FREE,
    )

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(pricing=None),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted_pricing is None


async def test_update_endpoint_active_rejects_clearing_paid_price_without_mutating_state(
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
    await create_endpoint_price_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency="USD",
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(pricing=None),
            )
        await session.commit()

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted_pricing is not None
    assert persisted_pricing.amount_minor == 500
    assert persisted_pricing.currency == "USD"


async def test_update_endpoint_active_rejects_free_to_paid_without_price(
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
        access_mode=AccessMode.FREE,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await update_endpoint(
                session=session,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(access_mode=AccessMode.PAID),
            )


async def test_update_endpoint_active_free_to_paid_with_price_creates_revision(
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
        access_mode=AccessMode.FREE,
    )

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(
                access_mode=AccessMode.PAID,
                pricing=FixedPrice(amount_minor=250, currency="USD"),
            ),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )

    assert persisted_pricing is not None
    assert persisted_pricing.amount_minor == 250
    assert revision_count == 2


async def test_update_endpoint_active_price_replacement_creates_revision(
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
    await create_endpoint_price_record(
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
            changes=EndpointUpdateRequest(pricing=FixedPrice(amount_minor=999, currency="GBP")),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )

    assert persisted_pricing is not None
    assert persisted_pricing.amount_minor == 999
    assert persisted_pricing.currency == "GBP"
    assert revision_count == 2


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
            changes=EndpointUpdateRequest(timeout_seconds=60),
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
            changes=EndpointUpdateRequest(name="Renamed Endpoint"),
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
                changes=EndpointUpdateRequest(timeout_seconds=60),
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
                changes=EndpointUpdateRequest(timeout_seconds=60),
            )
        await session.commit()

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

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
                changes=EndpointUpdateRequest(timeout_seconds=60),
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
            changes=EndpointUpdateRequest(name="Renamed While Suspended"),
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
                changes=EndpointUpdateRequest(name="New Name"),
            )


async def test_update_endpoint_draft_identical_values_leave_updated_at_unchanged(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(
        db_session_factory,
        provider_account_id=account_id,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        name="Translate",
        timeout_seconds=30,
    )

    async with db_session_factory() as session:
        seeded = await session.get(ServiceEndpoint, endpoint_id)
        assert seeded is not None
        seeded_updated_at = seeded.updated_at

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(name="Translate", timeout_seconds=30),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted is not None
    assert persisted.updated_at == seeded_updated_at


async def test_update_endpoint_active_identical_material_value_is_a_no_op(
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
        timeout_seconds=30,
    )

    async with db_session_factory() as session:
        seeded_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        seeded_service = await session.get(Service, service_id)
        assert seeded_endpoint is not None
        assert seeded_service is not None
        seeded_updated_at = seeded_endpoint.updated_at
        seeded_token = seeded_service.current_change_token

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(timeout_seconds=30),
        )

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        persisted_service = await session.get(Service, service_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )

    assert persisted_endpoint is not None
    assert persisted_service is not None
    assert persisted_endpoint.updated_at == seeded_updated_at
    assert persisted_service.current_change_token == seeded_token
    assert revision_count == 1


async def test_update_endpoint_normalized_value_matching_stored_value_is_a_no_op(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(
        db_session_factory,
        provider_account_id=account_id,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        name="Same",
    )

    async with db_session_factory() as session:
        seeded = await session.get(ServiceEndpoint, endpoint_id)
        assert seeded is not None
        seeded_updated_at = seeded.updated_at

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(name="  Same  "),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted is not None
    assert persisted.name == "Same"
    assert persisted.updated_at == seeded_updated_at


async def test_update_endpoint_active_unchanged_material_field_creates_no_revision(
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
        name="Original Name",
        timeout_seconds=30,
    )

    async with db_session_factory() as session:
        seeded_service = await session.get(Service, service_id)
        assert seeded_service is not None
        seeded_token = seeded_service.current_change_token

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(name="Renamed Endpoint", timeout_seconds=30),
        )

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        persisted_service = await session.get(Service, service_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )

    assert persisted_endpoint is not None
    assert persisted_service is not None
    assert persisted_endpoint.name == "Renamed Endpoint"
    assert revision_count == 1
    assert persisted_service.current_change_token == seeded_token


async def test_update_endpoint_identical_pricing_leaves_price_row_untouched(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(
        db_session_factory,
        provider_account_id=account_id,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency="USD",
    )

    async with db_session_factory() as session:
        seeded_pricing = await session.get(EndpointPrice, endpoint_id)
        seeded_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        assert seeded_pricing is not None
        assert seeded_endpoint is not None
        seeded_pricing_updated_at = seeded_pricing.updated_at
        seeded_endpoint_updated_at = seeded_endpoint.updated_at

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(pricing=FixedPrice(amount_minor=500, currency="USD")),
        )

    async with db_session_factory() as session:
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted_pricing is not None
    assert persisted_endpoint is not None
    assert persisted_pricing.updated_at == seeded_pricing_updated_at
    assert persisted_endpoint.updated_at == seeded_endpoint_updated_at


async def test_update_endpoint_null_pricing_on_unpriced_paid_endpoint_is_a_no_op(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(
        db_session_factory,
        provider_account_id=account_id,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )

    async with db_session_factory() as session:
        seeded = await session.get(ServiceEndpoint, endpoint_id)
        assert seeded is not None
        seeded_updated_at = seeded.updated_at

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(pricing=None),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted is not None
    assert persisted_pricing is None
    assert persisted.updated_at == seeded_updated_at


async def test_update_endpoint_active_paid_to_free_without_price_row_revises_for_mode_change(
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
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(access_mode=AccessMode.FREE),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )

    assert persisted is not None
    assert persisted.access_mode is AccessMode.FREE
    assert persisted_pricing is None
    # access_mode is itself material, so the revision comes from the mode change
    # alone: no pricing change was recorded because there was no price row.
    assert revision_count == 2


async def test_update_endpoint_active_paid_to_free_deletes_price_when_pricing_omitted(
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
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(access_mode=AccessMode.FREE),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
        persisted_pricing = await session.get(EndpointPrice, endpoint_id)

    assert persisted is not None
    assert persisted.access_mode is AccessMode.FREE
    assert persisted_pricing is None


async def test_update_endpoint_suspended_service_allows_no_op_update(
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
        timeout_seconds=30,
    )
    await create_moderation_action_record(
        db_session_factory,
        service_id=service_id,
        action="suspend",
    )

    async with db_session_factory() as session:
        seeded = await session.get(ServiceEndpoint, endpoint_id)
        assert seeded is not None
        seeded_updated_at = seeded.updated_at

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(timeout_seconds=30),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted is not None
    assert persisted.updated_at == seeded_updated_at


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
            request=EndpointUpstreamRequest(
                base_url=HttpUrl("http://127.0.0.1:9000"),
                path="  /translate  ",
                http_method="POST",
                config=UPSTREAM_CONFIG,
            ),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert persisted is not None
    assert persisted.base_url == "http://127.0.0.1:9000/"
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
            request=EndpointUpstreamRequest(
                base_url=HttpUrl("http://127.0.0.1:9000"),
                path="/translate",
                http_method="POST",
                config=UPSTREAM_CONFIG,
            ),
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
            request=EndpointUpstreamRequest(
                base_url=HttpUrl("http://127.0.0.1:9100"),
                path="/summarize",
                http_method="PUT",
                config={"headers": {}},
            ),
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
    assert persisted.base_url == "http://127.0.0.1:9100/"
    assert persisted.path == "/summarize"
    assert persisted.http_method == "PUT"
    assert persisted.config == {"headers": {}}
    assert persisted.updated_at > first_updated_at


async def test_upsert_upstream_ignores_identical_state_without_touching_updated_at(
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
    await create_upstream_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        base_url="http://127.0.0.1:9000/",
        path="/translate",
        http_method="POST",
        config=dict(UPSTREAM_CONFIG),
    )

    async with db_session_factory() as session:
        before = await session.get(ProviderUpstream, endpoint_id)
        assert before is not None
        before_updated_at = before.updated_at

    async with db_session_factory() as session:
        await upsert_upstream(
            session=session,
            settings=_upstream_settings(),
            account_id=account_id,
            endpoint_id=endpoint_id,
            request=EndpointUpstreamRequest(
                base_url=HttpUrl("http://127.0.0.1:9000"),
                path=" /translate ",
                http_method="POST",
                config=UPSTREAM_CONFIG,
            ),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert persisted is not None
    assert persisted.updated_at == before_updated_at


async def test_upsert_upstream_identical_state_on_active_service_returns_normally(
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
    await create_upstream_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        base_url="http://127.0.0.1:9000/",
        path="/translate",
        http_method="POST",
        config=dict(UPSTREAM_CONFIG),
    )

    async with db_session_factory() as session:
        await upsert_upstream(
            session=session,
            settings=_upstream_settings(),
            account_id=account_id,
            endpoint_id=endpoint_id,
            request=EndpointUpstreamRequest(
                base_url=HttpUrl("http://127.0.0.1:9000"),
                path="/translate",
                http_method="POST",
                config=UPSTREAM_CONFIG,
            ),
        )


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
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl("http://127.0.0.1:9000"),
                    path="/translate",
                    http_method="POST",
                    config={},
                ),
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
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl("http://127.0.0.1:9000"),
                    path="/translate",
                    http_method="POST",
                    config={},
                ),
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
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl("http://127.0.0.1:9000"),
                    path="/translate",
                    http_method="POST",
                    config={},
                ),
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
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl("https://127.0.0.1:9000"),
                    path="/translate",
                    http_method="POST",
                    config={},
                ),
            )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert persisted is None


async def test_upsert_upstream_validates_input_before_resolving_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await upsert_upstream(
                session=session,
                settings=_upstream_settings(),
                account_id=account_id,
                endpoint_id=999_999,
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl("https://127.0.0.1:9000"),
                    path="/translate",
                    http_method="POST",
                    config={},
                ),
            )
