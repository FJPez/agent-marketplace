import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import AccessMode, PricingModelType
from app.db.models import Account, ProviderProfile, Service
from app.repositories.pricing_model_repo import PricingModelRepository
from app.repositories.service_endpoint_repo import ServiceEndpointRepository


async def _create_provider_account(
    session: AsyncSession,
    *,
    display_name: str,
) -> int:
    account = Account()
    session.add(account)
    await session.flush()
    session.add(ProviderProfile(account_id=account.id, display_name=display_name))
    return account.id


@pytest.mark.asyncio
async def test_pricing_model_repository_upserts_fixed_per_call_and_free_pricing(
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
            description=None,
        )
        session.add(service)
        await session.flush()
        endpoint_repo = ServiceEndpointRepository(session)
        pricing_repo = PricingModelRepository(session)
        endpoint = endpoint_repo.add(
            service_id=service.id,
            key="translate",
            name="Translate",
            summary="Endpoint summary",
            description=None,
            access_mode=AccessMode.PAID,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        await session.flush()

        pricing_repo.upsert_fixed_per_call(
            endpoint,
            amount_minor=2500,
            currency="USD",
        )
        pricing_repo.upsert_free(endpoint)

    async with db_session_factory() as session:
        endpoint_repo = ServiceEndpointRepository(session)
        persisted = await endpoint_repo.get_owned(
            endpoint_id=endpoint.id,
            provider_account_id=provider_account_id,
        )

    assert persisted is not None
    assert persisted.pricing is not None
    assert persisted.pricing.pricing_type is PricingModelType.FREE
    assert persisted.pricing.amount_minor is None
    assert persisted.pricing.currency is None
