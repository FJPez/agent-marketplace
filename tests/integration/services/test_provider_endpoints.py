import re
from urllib.parse import urlsplit

import pytest
from pydantic import HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_record,
    create_listing_price_record,
    create_moderation_action_record,
    create_provider_account_record,
    create_service_record,
    create_upstream_record,
)
from tests.fixtures.settings import build_service_settings
from tests.helpers.dns import (
    TEST_UPSTREAM_ADDRESS,
    TEST_UPSTREAM_BASE_URL,
    TEST_UPSTREAM_HOST,
    FakeResolver,
    TransactionWatchingResolver,
)
from tests.helpers.request_validation import (
    IN_PROCESS_REQUEST_VALIDATION_POOL,
    TransactionWatchingPool,
)

from app.core.enums import AccessMode, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.core.json_types import JsonObject
from app.db.models import ProviderUpstream, Service, ServiceEndpoint, ServiceRevision
from app.schemas.service import (
    EndpointCreateRequest,
    EndpointResponse,
    EndpointUpdateRequest,
    EndpointUpstreamRequest,
)
from app.services.provider_endpoints import (
    MAX_UPSTREAM_HOSTS_PER_SERVICE,
    create_endpoint,
    update_endpoint,
    upsert_upstream,
)

pytestmark = [pytest.mark.asyncio]

REQUEST_SCHEMA: JsonObject = {"type": "object", "properties": {"text": {"type": "string"}}}
RESPONSE_SCHEMA: JsonObject = {"type": "object", "properties": {"result": {"type": "string"}}}
NEGATIVE_LENGTH = (
    "request_schema is not a valid JSON Schema: -1 is less than the minimum of 0 at /minLength"
)


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


async def test_create_endpoint_persists_normalized_fields(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        endpoint = await create_endpoint(
            session=session,
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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

    assert persisted_endpoint is not None
    assert persisted_endpoint.service_id == service_id
    assert persisted_endpoint.key == "free-ping"
    assert persisted_endpoint.name == "Free Ping"
    assert persisted_endpoint.summary == "A summary"
    assert persisted_endpoint.description == "A description"
    assert persisted_endpoint.access_mode is AccessMode.FREE
    assert persisted_endpoint.timeout_seconds == 30
    assert persisted_endpoint.is_enabled is True


async def test_create_endpoint_persists_invocation_fields(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        endpoint = await create_endpoint(
            session=session,
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
            account_id=account_id,
            service_id=service_id,
            request=EndpointCreateRequest(
                key="plain-text",
                name="Plain Text",
                access_mode=AccessMode.FREE,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                response_content_type="text/plain",
                timeout_seconds=30,
                supports_idempotency=True,
            ),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint.id)

    assert persisted is not None
    assert persisted.response_content_type == "text/plain"
    assert persisted.supports_idempotency is True
    assert persisted.timeout_seconds == 30


async def test_create_endpoint_returns_endpoint_with_loaded_relations(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        created = await create_endpoint(
            session=session,
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
    assert created.current_price is None
    assert created.upstream is None


async def test_create_endpoint_rejects_duplicate_key_same_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        await create_endpoint(
            session=session,
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
                settings=build_service_settings(),
                validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
                settings=build_service_settings(),
                validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
        watching = TransactionWatchingPool(session)
        with pytest.raises(NotFoundError, match=r"^service not found$"):
            await create_endpoint(
                session=session,
                settings=build_service_settings(),
                validation_pool=watching,
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

    # Another account's save never reaches the request validation workers.
    assert watching.in_transaction_during_compiles == []


async def test_create_endpoint_compiles_its_request_schema_with_no_transaction_open(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        watching = TransactionWatchingPool(session)
        await create_endpoint(
            session=session,
            settings=build_service_settings(),
            validation_pool=watching,
            account_id=account_id,
            service_id=service_id,
            request=EndpointCreateRequest(
                key="new-endpoint",
                name="New Endpoint",
                access_mode=AccessMode.FREE,
                request_schema=REQUEST_SCHEMA,
                response_schema=RESPONSE_SCHEMA,
                timeout_seconds=30,
            ),
        )

    assert watching.in_transaction_during_compiles == [False]


async def test_create_endpoint_refuses_a_request_schema_that_does_not_compile(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match=f"^{re.escape(NEGATIVE_LENGTH)}$"):
            await create_endpoint(
                session=session,
                settings=build_service_settings(),
                validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                service_id=service_id,
                request=EndpointCreateRequest(
                    key="translate",
                    name="Translate",
                    access_mode=AccessMode.FREE,
                    request_schema={"minLength": -1},
                    response_schema=RESPONSE_SCHEMA,
                    timeout_seconds=30,
                ),
            )

    async with db_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ServiceEndpoint)) == 0


async def test_update_endpoint_refuses_a_request_schema_that_does_not_compile(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match=f"^{re.escape(NEGATIVE_LENGTH)}$"):
            await update_endpoint(
                session=session,
                settings=build_service_settings(),
                validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(request_schema={"minLength": -1}),
            )

    async with db_session_factory() as session:
        endpoint = await session.get(ServiceEndpoint, endpoint_id)
    assert endpoint is not None
    assert endpoint.request_schema == {"type": "object"}


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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(
                name="  New Name  ", summary="  New Summary  ", timeout_seconds=20
            ),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted is not None
    assert persisted.name == "New Name"
    assert persisted.summary == "New Summary"
    assert persisted.timeout_seconds == 20
    assert persisted.updated_at > before_updated_at


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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(timeout_seconds=20),
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


@pytest.mark.parametrize(
    ("field", "value"),
    [("supports_idempotency", True), ("response_content_type", "text/plain")],
)
async def test_update_endpoint_active_invocation_field_change_creates_revision(
    db_session_factory: async_sessionmaker[AsyncSession],
    field: str,
    value: object,
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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest.model_validate({field: value}),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
        latest_revision = await session.scalar(
            select(ServiceRevision)
            .where(ServiceRevision.service_id == service_id)
            .order_by(ServiceRevision.revision_number.desc())
            .limit(1),
        )

    assert persisted is not None
    assert getattr(persisted, field) == value
    assert latest_revision is not None
    assert latest_revision.revision_number == 2


async def test_update_endpoint_active_material_update_snapshots_sibling_endpoints(
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
    updated_endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )
    sibling_endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="summarize",
        access_mode=AccessMode.PAID,
    )
    sibling_price_id = await create_listing_price_record(
        db_session_factory,
        endpoint_id=sibling_endpoint_id,
    )

    async with db_session_factory() as session:
        await update_endpoint(
            session=session,
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
            account_id=account_id,
            endpoint_id=updated_endpoint_id,
            changes=EndpointUpdateRequest(timeout_seconds=20),
        )

    async with db_session_factory() as session:
        latest_revision = await session.scalar(
            select(ServiceRevision)
            .where(ServiceRevision.service_id == service_id)
            .order_by(ServiceRevision.revision_number.desc())
            .limit(1),
        )

    assert latest_revision is not None
    # Ordered by (key, id): "summarize" before "translate".
    assert latest_revision.snapshot["endpoints"] == [
        {
            "id": sibling_endpoint_id,
            "key": "summarize",
            "access_mode": "paid",
            "request_schema": {"type": "object"},
            "response_schema": {"type": "object"},
            "response_content_type": "application/json",
            "price": {"id": sibling_price_id, "version": 1},
            "timeout_seconds": 30,
            "supports_idempotency": False,
            "is_enabled": True,
        },
        {
            "id": updated_endpoint_id,
            "key": "translate",
            "access_mode": "free",
            "request_schema": {"type": "object"},
            "response_schema": {"type": "object"},
            "response_content_type": "application/json",
            "price": None,
            "timeout_seconds": 20,
            "supports_idempotency": False,
            "is_enabled": True,
        },
    ]


async def test_update_endpoint_returns_endpoint_renderable_without_lazy_loading(
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
    await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id)
    await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        endpoint = await update_endpoint(
            session=session,
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(timeout_seconds=20),
        )
        response = EndpointResponse.from_model(endpoint)

    assert response.id == endpoint_id
    assert response.timeout_seconds == 20
    assert response.has_upstream is True
    assert response.price is not None
    assert response.price.version == 1
    assert response.price.amount == 250_000


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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
                settings=build_service_settings(),
                validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(timeout_seconds=20),
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
                settings=build_service_settings(),
                validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(timeout_seconds=20),
            )
        await session.commit()

    async with db_session_factory() as session:
        persisted_endpoint = await session.get(ServiceEndpoint, endpoint_id)

    assert persisted_endpoint is not None
    assert persisted_endpoint.timeout_seconds == 30
    assert persisted_endpoint.updated_at == seeded_updated_at
    assert persisted_endpoint.current_price_id is None


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
                settings=build_service_settings(),
                validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(timeout_seconds=20),
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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
        watching = TransactionWatchingPool(session)
        with pytest.raises(NotFoundError, match=r"^endpoint not found$"):
            await update_endpoint(
                session=session,
                settings=build_service_settings(),
                validation_pool=watching,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(name="New Name", request_schema=REQUEST_SCHEMA),
            )

    # Another account's save never reaches the request validation workers.
    assert watching.in_transaction_during_compiles == []


async def test_update_endpoint_compiles_its_request_schema_with_no_transaction_open(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        watching = TransactionWatchingPool(session)
        await update_endpoint(
            session=session,
            settings=build_service_settings(),
            validation_pool=watching,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(request_schema=REQUEST_SCHEMA),
        )

    assert watching.in_transaction_during_compiles == [False]


async def test_update_endpoint_refuses_to_enable_an_endpoint_without_an_upstream_when_active(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    # Published disabled, and upstreams cannot be added outside draft.
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        is_enabled=False,
    )

    async with db_session_factory() as session:
        with pytest.raises(
            InvalidStateError,
            match=r"^an endpoint of an active service cannot be enabled without an upstream$",
        ):
            await update_endpoint(
                session=session,
                settings=build_service_settings(),
                validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(is_enabled=True),
            )

    async with db_session_factory() as session:
        endpoint = await session.get(ServiceEndpoint, endpoint_id)
    assert endpoint is not None
    assert endpoint.is_enabled is False


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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
            settings=build_service_settings(),
            validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
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
    dns_resolver: FakeResolver,
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
            resolver=dns_resolver,
            account_id=account_id,
            endpoint_id=endpoint_id,
            request=EndpointUpstreamRequest(
                base_url=HttpUrl(TEST_UPSTREAM_BASE_URL),
                path="  /translate  ",
                http_method="POST",
            ),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert persisted is not None
    assert persisted.base_url == TEST_UPSTREAM_BASE_URL
    assert persisted.path == "/translate"
    assert persisted.http_method == "POST"


async def test_upsert_upstream_replaces_existing_row_in_place(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
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
            resolver=dns_resolver,
            account_id=account_id,
            endpoint_id=endpoint_id,
            request=EndpointUpstreamRequest(
                base_url=HttpUrl(TEST_UPSTREAM_BASE_URL),
                path="/translate",
                http_method="POST",
            ),
        )

    async with db_session_factory() as session:
        first = await session.get(ProviderUpstream, endpoint_id)
        assert first is not None
        first_updated_at = first.updated_at

    async with db_session_factory() as session:
        await upsert_upstream(
            session=session,
            resolver=dns_resolver,
            account_id=account_id,
            endpoint_id=endpoint_id,
            request=EndpointUpstreamRequest(
                base_url=HttpUrl(f"https://{TEST_UPSTREAM_HOST}/v2"),
                path="/summarize",
                http_method="PUT",
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
    assert persisted.base_url == f"https://{TEST_UPSTREAM_HOST}/v2"
    assert persisted.path == "/summarize"
    assert persisted.http_method == "PUT"
    assert persisted.updated_at > first_updated_at


async def test_upsert_upstream_ignores_identical_state_without_touching_updated_at(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
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
        base_url=TEST_UPSTREAM_BASE_URL,
        path="/translate",
        http_method="POST",
    )

    async with db_session_factory() as session:
        before = await session.get(ProviderUpstream, endpoint_id)
        assert before is not None
        before_updated_at = before.updated_at

    async with db_session_factory() as session:
        await upsert_upstream(
            session=session,
            resolver=dns_resolver,
            account_id=account_id,
            endpoint_id=endpoint_id,
            request=EndpointUpstreamRequest(
                base_url=HttpUrl(TEST_UPSTREAM_BASE_URL),
                path=" /translate ",
                http_method="POST",
            ),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert persisted is not None
    assert persisted.updated_at == before_updated_at


async def test_upsert_upstream_identical_state_on_active_service_returns_normally(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
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
        base_url=TEST_UPSTREAM_BASE_URL,
        path="/translate",
        http_method="POST",
    )

    async with db_session_factory() as session:
        await upsert_upstream(
            session=session,
            resolver=dns_resolver,
            account_id=account_id,
            endpoint_id=endpoint_id,
            request=EndpointUpstreamRequest(
                base_url=HttpUrl(TEST_UPSTREAM_BASE_URL),
                path="/translate",
                http_method="POST",
            ),
        )


async def test_upsert_upstream_rejects_active_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
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
                resolver=dns_resolver,
                account_id=account_id,
                endpoint_id=endpoint_id,
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl(TEST_UPSTREAM_BASE_URL),
                    path="/translate",
                    http_method="POST",
                ),
            )


async def test_upsert_upstream_raises_not_found_for_missing_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await upsert_upstream(
                session=session,
                resolver=dns_resolver,
                account_id=account_id,
                endpoint_id=999_999,
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl(TEST_UPSTREAM_BASE_URL),
                    path="/translate",
                    http_method="POST",
                ),
            )


async def test_upsert_upstream_raises_not_found_for_other_accounts_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
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
                resolver=dns_resolver,
                account_id=account_id,
                endpoint_id=endpoint_id,
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl(TEST_UPSTREAM_BASE_URL),
                    path="/translate",
                    http_method="POST",
                ),
            )


async def test_upsert_upstream_rejects_a_host_resolving_to_a_private_address(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)
    dns_resolver.addresses[TEST_UPSTREAM_HOST] = [TEST_UPSTREAM_ADDRESS, "10.0.0.1"]

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match="must resolve, and only to public addresses"):
            await upsert_upstream(
                session=session,
                resolver=dns_resolver,
                account_id=account_id,
                endpoint_id=endpoint_id,
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl(f"https://{TEST_UPSTREAM_HOST}"),
                    path="/translate",
                    http_method="POST",
                ),
            )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert persisted is None


async def test_upsert_upstream_resolves_the_host_with_no_transaction_open(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
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
        watching = TransactionWatchingResolver(dns_resolver, session)
        await upsert_upstream(
            session=session,
            resolver=watching,
            account_id=account_id,
            endpoint_id=endpoint_id,
            request=EndpointUpstreamRequest(
                base_url=HttpUrl(f"https://{TEST_UPSTREAM_HOST}"),
                path="/translate",
                http_method="POST",
            ),
        )

    async with db_session_factory() as session:
        persisted = await session.get(ProviderUpstream, endpoint_id)

    assert watching.in_transaction_during_lookups == [False]
    assert persisted is not None


async def test_upsert_upstream_caps_the_distinct_hosts_of_a_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await _create_draft_service(db_session_factory, provider_account_id=account_id)
    hosts = [
        f"api{number}.provider.example" for number in range(MAX_UPSTREAM_HOSTS_PER_SERVICE + 1)
    ]
    for host in hosts:
        dns_resolver.addresses[host] = [TEST_UPSTREAM_ADDRESS]
    # Every host but the last two is already stored, one per endpoint.
    for number, host in enumerate(hosts[:-2]):
        endpoint_id = await create_endpoint_record(
            db_session_factory,
            service_id=service_id,
            key=f"stored-{number}",
        )
        await create_upstream_record(
            db_session_factory,
            endpoint_id=endpoint_id,
            base_url=f"https://{host}/",
        )
    last_id = await create_endpoint_record(db_session_factory, service_id=service_id, key="last")
    extra_id = await create_endpoint_record(db_session_factory, service_id=service_id, key="extra")

    async def upsert(endpoint_id: int, host: str) -> None:
        async with db_session_factory() as session:
            await upsert_upstream(
                session=session,
                resolver=dns_resolver,
                account_id=account_id,
                endpoint_id=endpoint_id,
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl(f"https://{host}"),
                    path="/translate",
                    http_method="POST",
                ),
            )

    await upsert(last_id, hosts[-2])
    with pytest.raises(
        InvalidInputError,
        match=(
            f"a service's upstreams can name at most {MAX_UPSTREAM_HOSTS_PER_SERVICE} "
            "distinct hosts; point this endpoint at one the service already uses"
        ),
    ):
        await upsert(extra_id, hosts[-1])
    await upsert(extra_id, hosts[0])
    # The last endpoint's own host no longer counts once it moves, so it may move to a
    # new host.
    await upsert(last_id, hosts[-1])

    async with db_session_factory() as session:
        stored = await session.scalars(
            select(ProviderUpstream.base_url)
            .join(ServiceEndpoint)
            .where(ServiceEndpoint.service_id == service_id),
        )
        stored_hosts = {urlsplit(base_url).hostname for base_url in stored}
    assert len(stored_hosts) == MAX_UPSTREAM_HOSTS_PER_SERVICE
    assert hosts[-2] not in stored_hosts


async def test_upsert_upstream_validates_input_before_resolving_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await upsert_upstream(
                session=session,
                resolver=dns_resolver,
                account_id=account_id,
                endpoint_id=999_999,
                request=EndpointUpstreamRequest(
                    base_url=HttpUrl("https://127.0.0.1:9000"),
                    path="/translate",
                    http_method="POST",
                ),
            )
