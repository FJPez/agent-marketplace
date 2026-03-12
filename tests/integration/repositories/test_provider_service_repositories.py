import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import AccessMode
from app.db.models import Account, ProviderProfile, Service
from app.repositories.provider_upstream_repo import ProviderUpstreamRepository
from app.repositories.service_endpoint_repo import ServiceEndpointRepository
from app.repositories.service_repo import ServiceRepository


async def _create_provider_account(
    session: AsyncSession,
    *,
    display_name: str,
) -> int:
    account = Account()
    session.add(account)
    await session.flush()
    session.add(
        ProviderProfile(account_id=account.id, display_name=display_name),
    )
    return account.id


@pytest.mark.asyncio
async def test_service_repository_persists_service_tags_endpoints_and_upstream(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        provider_account_id = await _create_provider_account(
            session,
            display_name="Provider",
        )
        service_repo = ServiceRepository(session)
        endpoint_repo = ServiceEndpointRepository(session)
        upstream_repo = ProviderUpstreamRepository()

        service = service_repo.add(
            provider_account_id=provider_account_id,
            slug="translation-service",
            name="Translation Service",
            summary="Summary",
            description="Description",
        )
        await session.flush()
        await service_repo.replace_tags(service, tags=["nlp", "translation"])
        endpoint = endpoint_repo.add(
            service_id=service.id,
            key="translate",
            name="Translate",
            summary="Endpoint summary",
            description="Endpoint description",
            access_mode=AccessMode.FREE,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        await session.flush()
        upstream_repo.upsert(
            endpoint,
            base_url="https://provider.internal",
            path="/translate",
            http_method="POST",
            config={"auth": {"type": "bearer"}},
        )

    async with db_session_factory() as session:
        repo = ServiceRepository(session)
        persisted = await repo.get_owned(
            service_id=service.id,
            provider_account_id=provider_account_id,
        )

    assert persisted is not None
    assert persisted.slug == "translation-service"
    assert sorted(tag.tag for tag in persisted.tags) == ["nlp", "translation"]
    assert len(persisted.endpoints) == 1
    assert persisted.endpoints[0].key == "translate"
    assert persisted.endpoints[0].upstream is not None


@pytest.mark.asyncio
async def test_service_repository_lists_only_owned_services_newest_first(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        owner_account_id = await _create_provider_account(
            session,
            display_name="Owner",
        )
        other_account_id = await _create_provider_account(
            session,
            display_name="Other",
        )
        repo = ServiceRepository(session)
        repo.add(
            provider_account_id=owner_account_id,
            slug="older-service",
            name="Older",
            summary="Older summary",
            description=None,
        )
        await session.flush()
        repo.add(
            provider_account_id=owner_account_id,
            slug="newer-service",
            name="Newer",
            summary="Newer summary",
            description=None,
        )
        repo.add(
            provider_account_id=other_account_id,
            slug="other-service",
            name="Other",
            summary="Other summary",
            description=None,
        )

    async with db_session_factory() as session:
        repo = ServiceRepository(session)
        services = await repo.list_by_provider_account_id(
            provider_account_id=owner_account_id,
        )

    assert [service.slug for service in services] == ["newer-service", "older-service"]


@pytest.mark.asyncio
async def test_service_endpoint_repository_updates_owned_endpoint(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        provider_account_id = await _create_provider_account(
            session,
            display_name="Provider",
        )
        service = Service(
            provider_account_id=provider_account_id,
            slug="translation-service",
            name="Translation Service",
            summary="Summary",
            description="Description",
        )
        session.add(service)
        await session.flush()
        endpoint_repo = ServiceEndpointRepository(session)
        endpoint = endpoint_repo.add(
            service_id=service.id,
            key="translate",
            name="Translate",
            summary="Endpoint summary",
            description="Endpoint description",
            access_mode=AccessMode.FREE,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        await session.flush()
        endpoint_id = endpoint.id

    async with db_session_factory.begin() as session:
        endpoint_repo = ServiceEndpointRepository(session)
        endpoint = await endpoint_repo.get_owned(
            endpoint_id=endpoint_id,
            provider_account_id=provider_account_id,
        )
        assert endpoint is not None
        endpoint_repo.update_endpoint(
            endpoint,
            name="Translate Updated",
            summary="Updated summary",
            access_mode=AccessMode.PAID,
            timeout_seconds=60,
            is_enabled=False,
        )

    async with db_session_factory() as session:
        endpoint_repo = ServiceEndpointRepository(session)
        updated = await endpoint_repo.get_owned(
            endpoint_id=endpoint_id,
            provider_account_id=provider_account_id,
        )

    assert updated is not None
    assert updated.name == "Translate Updated"
    assert updated.summary == "Updated summary"
    assert updated.access_mode is AccessMode.PAID
    assert updated.timeout_seconds == 60
    assert updated.is_enabled is False
