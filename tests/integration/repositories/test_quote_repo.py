from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models import Account, Quote, Service, ServiceEndpoint, ServiceRevision
from app.repositories.quote_repo import QuoteRepository


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(display_name="Provider")
        session.add(account)
        await session.flush()
        return account.id


async def _seed_service_and_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int, int]:
    provider_account_id = await _create_provider_account(db_session_factory)
    async with db_session_factory.begin() as session:
        service = Service(
            provider_account_id=provider_account_id,
            slug="quote-service",
            name="Quote Service",
            summary="Summary",
            description=None,
            lifecycle=ServiceLifecycle.ACTIVE,
        )
        session.add(service)
        await session.flush()
        endpoint = ServiceEndpoint(
            service_id=service.id,
            key="translate",
            name="Translate",
            summary=None,
            description=None,
            access_mode=AccessMode.PAID,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()
        revision = ServiceRevision(
            service_id=service.id,
            revision_number=1,
            change_token="c" * 64,
            snapshot={"slug": service.slug},
        )
        session.add(revision)
        await session.flush()
        return service.id, endpoint.id, revision.id


@pytest.mark.asyncio
async def test_quote_repository_persists_quote(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    service_id, endpoint_id, _ = await _seed_service_and_endpoint(db_session_factory)

    expires_at = datetime(2026, 3, 14, 12, 5, tzinfo=UTC)

    async with db_session_factory.begin() as session:
        repo = QuoteRepository(session)
        quote = repo.add(
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key="translate",
            request_hash="a" * 64,
            pricing_type=PricingModelType.FIXED_PER_CALL,
            amount_minor=500,
            currency="USD",
            service_revision_id=None,
            service_change_token=None,
            expires_at=expires_at,
        )
        await session.flush()

        assert quote.id is not None

    async with db_session_factory() as session:
        persisted = await session.get(Quote, quote.id)

    assert persisted is not None
    assert persisted.service_id == service_id
    assert persisted.endpoint_id == endpoint_id
    assert persisted.endpoint_key == "translate"
    assert persisted.request_hash == "a" * 64
    assert persisted.pricing_type is PricingModelType.FIXED_PER_CALL
    assert persisted.amount_minor == 500
    assert persisted.currency == "USD"
    assert persisted.expires_at == expires_at


@pytest.mark.asyncio
async def test_quote_repository_get_returns_quote_by_id(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    service_id, endpoint_id, revision_id = await _seed_service_and_endpoint(db_session_factory)

    async with db_session_factory.begin() as session:
        repo = QuoteRepository(session)
        quote = repo.add(
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key="translate",
            request_hash="b" * 64,
            pricing_type=PricingModelType.FREE,
            amount_minor=None,
            currency=None,
            service_revision_id=revision_id,
            service_change_token="c" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        await session.flush()
        quote_id = quote.id

    async with db_session_factory() as session:
        repo = QuoteRepository(session)
        loaded = await repo.get(quote_id=quote_id)

    assert loaded is not None
    assert loaded.id == quote_id
    assert loaded.service_revision_id == revision_id
    assert loaded.service_change_token == "c" * 64
