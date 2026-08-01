import asyncio

import pytest
from pydantic import HttpUrl, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_record,
    create_pricing_record,
    create_provider_account_record,
    create_service_record,
    create_upstream_record,
)

from app.core.actor import ActorContext
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models import Service, ServiceRevision
from app.repositories.service_repo import ServiceRepository
from app.repositories.service_revision_repo import ServiceRevisionRepository
from app.schemas.service import EndpointUpstreamRequest
from app.services import provider_endpoints
from app.services.provider_endpoint_service import ProviderEndpointService
from app.services.publish_service import PublishService


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    return await create_provider_account_record(
        db_session_factory,
        display_name="Provider",
    )


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str,
    lifecycle: ServiceLifecycle,
) -> int:
    return await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        lifecycle=lifecycle,
    )


async def _seed_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    key: str = "translate",
    access_mode: AccessMode = AccessMode.FREE,
) -> int:
    return await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key=key,
        access_mode=access_mode,
    )


async def _seed_fixed_price(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    amount_minor: int = 1500,
    currency: str = "USD",
) -> None:
    await create_pricing_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=amount_minor,
        currency=currency,
    )


async def _seed_upstream(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    path: str = "/invoke",
) -> None:
    await create_upstream_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        path=path,
    )


@pytest.mark.asyncio
async def test_concurrent_active_endpoint_updates_create_distinct_revisions(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = migrated_database
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="concurrent-active-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )

    original_next_revision_number = ServiceRevisionRepository.next_revision_number
    first_revision_lookup = asyncio.Event()
    release_first_revision_lookup = asyncio.Event()
    next_revision_lookup_calls = 0

    async def delayed_next_revision_number(
        self: ServiceRevisionRepository,
        *,
        service_id: int,
    ) -> int:
        nonlocal next_revision_lookup_calls
        revision_number = await original_next_revision_number(self, service_id=service_id)
        next_revision_lookup_calls += 1
        if next_revision_lookup_calls == 1:
            first_revision_lookup.set()
            await release_first_revision_lookup.wait()
        return revision_number

    monkeypatch.setattr(
        ServiceRevisionRepository,
        "next_revision_number",
        delayed_next_revision_number,
    )

    async def update_timeout(timeout_seconds: int) -> None:
        async with db_session_factory() as session:
            await provider_endpoints.update_endpoint(
                session=session,
                account_id=provider_account_id,
                endpoint_id=endpoint_id,
                updates={"timeout_seconds": timeout_seconds},
            )

    first_update = asyncio.create_task(update_timeout(45))
    await first_revision_lookup.wait()
    second_update = asyncio.create_task(update_timeout(60))
    release_first_revision_lookup.set()
    await asyncio.gather(first_update, second_update)

    async with db_session_factory() as session:
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )
        revisions = await ServiceRevisionRepository(session).list_by_service_id(
            service_id=service_id,
        )

    assert revision_count == 2
    assert [revision.revision_number for revision in revisions] == [2, 1]


@pytest.mark.asyncio
async def test_publish_serializes_with_concurrent_draft_upstream_mutation(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = migrated_database
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="publish-race-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.FREE,
    )
    await _seed_upstream(db_session_factory, endpoint_id=endpoint_id)

    publish_has_lock = asyncio.Event()
    mutation_lookup_started = asyncio.Event()
    release_publish = asyncio.Event()
    original_get_owned_for_update = ServiceRepository.get_owned_for_update
    owned_lookup_calls = 0

    async def delayed_get_owned_for_update(
        self: ServiceRepository,
        *,
        service_id: int,
        provider_account_id: int,
    ) -> Service | None:
        nonlocal owned_lookup_calls
        owned_lookup_calls += 1
        if owned_lookup_calls == 2:
            mutation_lookup_started.set()
        service = await original_get_owned_for_update(
            self,
            service_id=service_id,
            provider_account_id=provider_account_id,
        )
        if owned_lookup_calls == 1 and service is not None and service.id == service_id:
            publish_has_lock.set()
            await release_publish.wait()
        return service

    monkeypatch.setattr(
        ServiceRepository,
        "get_owned_for_update",
        delayed_get_owned_for_update,
    )

    async def publish_service() -> None:
        async with db_session_factory() as session:
            service = PublishService(session)
            await service.publish_service(
                ActorContext(account_id=provider_account_id),
                service_id=service_id,
            )

    async def mutate_upstream() -> None:
        await publish_has_lock.wait()
        async with db_session_factory() as session:
            service = ProviderEndpointService(session)
            await service.upsert_upstream(
                ActorContext(account_id=provider_account_id),
                endpoint_id=endpoint_id,
                request=EndpointUpstreamRequest(
                    base_url=TypeAdapter(HttpUrl).validate_python(
                        "http://127.0.0.1:9000",
                    ),
                    path="/mutated",
                    http_method="POST",
                    config={},
                ),
            )

    publish_task = asyncio.create_task(publish_service())
    await publish_has_lock.wait()
    mutate_task = asyncio.create_task(mutate_upstream())
    await mutation_lookup_started.wait()
    release_publish.set()
    await publish_task
    await mutate_task

    async with db_session_factory() as session:
        service = await ServiceRepository(session).get_owned(
            service_id=service_id,
            provider_account_id=provider_account_id,
        )

    assert service is not None
    assert service.lifecycle is ServiceLifecycle.ACTIVE
    assert service.endpoints[0].upstream is not None
    assert service.endpoints[0].upstream.path == "/mutated"
