import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import AccessMode, InvocationStatus
from app.db.models import Account, ConsumerProfile, ProviderProfile, Service, ServiceEndpoint
from app.repositories.invocation_repo import InvocationRepository


@pytest.mark.asyncio
async def test_invocation_repository_persists_and_lists_by_consumer(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        provider_account = Account()
        consumer_account = Account()
        session.add_all([provider_account, consumer_account])
        await session.flush()
        session.add(ProviderProfile(account_id=provider_account.id, display_name="Provider"))
        session.add(ConsumerProfile(account_id=consumer_account.id, display_name="Consumer"))
        service = Service(
            provider_account_id=provider_account.id,
            slug="invoke-service",
            name="Invoke Service",
            summary="Summary",
            description=None,
        )
        session.add(service)
        await session.flush()
        endpoint = ServiceEndpoint(
            service_id=service.id,
            key="translate",
            name="Translate",
            summary=None,
            description=None,
            access_mode=AccessMode.FREE,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()

        repo = InvocationRepository(session)
        first = repo.add(
            consumer_account_id=consumer_account.id,
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key="translate",
            access_mode=AccessMode.FREE,
            quote_id=None,
            idempotency_key="key-1",
            request_hash="a" * 64,
            status=InvocationStatus.SUCCEEDED,
            response_payload={"result": "one"},
            upstream_status_code=200,
            error_message=None,
        )
        await session.flush()
        second = repo.add(
            consumer_account_id=consumer_account.id,
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key="translate",
            access_mode=AccessMode.FREE,
            quote_id=None,
            idempotency_key="key-2",
            request_hash="b" * 64,
            status=InvocationStatus.FAILED,
            response_payload=None,
            upstream_status_code=502,
            error_message="upstream request failed",
        )
        await session.flush()
        first_id = first.id
        second_id = second.id

    async with db_session_factory() as session:
        repo = InvocationRepository(session)
        loaded = await repo.get_for_consumer(
            invocation_id=first_id,
            consumer_account_id=consumer_account.id,
        )
        items = await repo.list_for_consumer(consumer_account_id=consumer_account.id)

    assert loaded is not None
    assert loaded.id == first_id
    assert [item.id for item in items] == [second_id, first_id]
    assert items[0].status is InvocationStatus.FAILED
