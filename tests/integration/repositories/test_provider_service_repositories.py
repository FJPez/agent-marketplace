import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import AccessMode
from app.db.models import Account, Service, ServiceEndpoint
from app.repositories.provider_upstream_repo import ProviderUpstreamRepository
from app.repositories.service_endpoint_repo import ServiceEndpointRepository
from app.repositories.service_repo import ServiceRepository


async def _create_provider_account(
    session: AsyncSession,
    *,
    display_name: str,
) -> int:
    account = Account(display_name=display_name)
    session.add(account)
    await session.flush()
    return account.id


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


@pytest.mark.asyncio
async def test_service_endpoint_repository_clears_nullable_fields_when_explicit_none(
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
            summary=None,
            description=None,
        )

    async with db_session_factory() as session:
        endpoint_repo = ServiceEndpointRepository(session)
        updated = await endpoint_repo.get_owned(
            endpoint_id=endpoint_id,
            provider_account_id=provider_account_id,
        )

    assert updated is not None
    assert updated.summary is None
    assert updated.description is None
    assert updated.name == "Translate"


@pytest.mark.asyncio
async def test_provider_upstream_repository_updates_existing_upstream_without_loading_relationship(
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
        upstream_repo = ProviderUpstreamRepository()
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
        service_id = service.id
        await upstream_repo.upsert(
            endpoint,
            base_url="https://provider.internal",
            path="/translate",
            http_method="POST",
            config={"auth": {"type": "bearer"}},
        )
        endpoint_id = endpoint.id

    async with db_session_factory.begin() as session:
        upstream_repo = ProviderUpstreamRepository()
        reloaded_endpoint = await session.get(ServiceEndpoint, endpoint_id)
        assert reloaded_endpoint is not None
        await upstream_repo.upsert(
            reloaded_endpoint,
            base_url="https://provider.internal",
            path="/translate/v2",
            http_method="PUT",
            config={"auth": {"type": "api_key"}},
        )

    async with db_session_factory() as session:
        repo = ServiceRepository(session)
        persisted = await repo.get_owned(
            service_id=service_id,
            provider_account_id=provider_account_id,
        )

    assert persisted is not None
    assert persisted.endpoints[0].upstream is not None
    assert persisted.endpoints[0].upstream.path == "/translate/v2"
    assert persisted.endpoints[0].upstream.http_method == "PUT"
    assert persisted.endpoints[0].upstream.config == {"auth": {"type": "api_key"}}
