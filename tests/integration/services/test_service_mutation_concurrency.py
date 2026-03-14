import asyncio

import pytest
from pydantic import HttpUrl, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.actor import ActorContext
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models import (
    Account,
    PricingModel,
    ProviderProfile,
    ProviderUpstream,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)
from app.repositories.service_repo import ServiceRepository
from app.repositories.service_revision_repo import ServiceRevisionRepository
from app.schemas.service import EndpointUpdateRequest, EndpointUpstreamRequest
from app.services.provider_endpoint_service import ProviderEndpointService
from app.services.provider_service_errors import ProviderServiceStateError
from app.services.publish_service import PublishService


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(
            ProviderProfile(account_id=account.id, display_name="Provider"),
        )
        return account.id


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str,
    lifecycle: ServiceLifecycle,
) -> int:
    async with db_session_factory.begin() as session:
        service = Service(
            provider_account_id=provider_account_id,
            slug=slug,
            name=f"{slug} name",
            summary=f"{slug} summary",
            description=f"{slug} description",
            lifecycle=lifecycle,
        )
        session.add(service)
        await session.flush()
        return service.id


async def _seed_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    key: str = "translate",
    access_mode: AccessMode = AccessMode.FREE,
) -> int:
    async with db_session_factory.begin() as session:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key=key,
            name=f"{key} name",
            summary=f"{key} summary",
            description=f"{key} description",
            access_mode=access_mode,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()
        return endpoint.id


async def _seed_fixed_price(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    amount_minor: int = 1500,
    currency: str = "USD",
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            PricingModel(
                endpoint_id=endpoint_id,
                pricing_type=PricingModelType.FIXED_PER_CALL,
                amount_minor=amount_minor,
                currency=currency,
            ),
        )


async def _seed_upstream(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    path: str = "/invoke",
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            ProviderUpstream(
                endpoint_id=endpoint_id,
                base_url="https://provider.internal",
                path=path,
                http_method="POST",
                config={},
            ),
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

    async def delayed_next_revision_number(
        self: ServiceRevisionRepository,
        *,
        service_id: int,
    ) -> int:
        revision_number = await original_next_revision_number(self, service_id=service_id)
        await asyncio.sleep(0.05)
        return revision_number

    monkeypatch.setattr(
        ServiceRevisionRepository,
        "next_revision_number",
        delayed_next_revision_number,
    )

    async def update_timeout(timeout_seconds: int) -> None:
        async with db_session_factory() as session:
            service = ProviderEndpointService(session)
            await service.update_endpoint(
                ActorContext(account_id=provider_account_id),
                endpoint_id=endpoint_id,
                request=EndpointUpdateRequest(timeout_seconds=timeout_seconds),
            )

    await asyncio.gather(
        update_timeout(45),
        update_timeout(60),
    )

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
async def test_publish_wins_over_concurrent_draft_upstream_mutation(
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
    release_publish = asyncio.Event()
    original_get_owned_for_update = ServiceRepository.get_owned_for_update

    async def delayed_get_owned_for_update(
        self: ServiceRepository,
        *,
        service_id: int,
        provider_account_id: int,
    ) -> Service | None:
        service = await original_get_owned_for_update(
            self,
            service_id=service_id,
            provider_account_id=provider_account_id,
        )
        if service is not None and service.id == service_id:
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
            with pytest.raises(
                ProviderServiceStateError,
                match="service is not mutable outside draft",
            ):
                await service.upsert_upstream(
                    ActorContext(account_id=provider_account_id),
                    endpoint_id=endpoint_id,
                    request=EndpointUpstreamRequest(
                        base_url=TypeAdapter(HttpUrl).validate_python(
                            "https://provider.internal",
                        ),
                        path="/mutated",
                        http_method="POST",
                        config={},
                    ),
                )

    publish_task = asyncio.create_task(publish_service())
    await publish_has_lock.wait()
    mutate_task = asyncio.create_task(mutate_upstream())
    release_publish.set()
    await publish_task
    await mutate_task
