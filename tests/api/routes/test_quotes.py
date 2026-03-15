from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models import (
    Account,
    PricingModel,
    ProviderProfile,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(ProviderProfile(account_id=account.id, display_name="Provider"))
        return account.id


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str,
    lifecycle: ServiceLifecycle = ServiceLifecycle.ACTIVE,
    with_revision: bool = False,
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

        if with_revision:
            revision = ServiceRevision(
                service_id=service.id,
                revision_number=1,
                change_token="a" * 64,
                snapshot={"slug": slug},
            )
            session.add(revision)
            await session.flush()
            service.current_revision_id = revision.id
            service.current_change_token = revision.change_token

        return service.id


async def _seed_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    key: str,
    access_mode: AccessMode = AccessMode.PAID,
    is_enabled: bool = True,
    pricing_type: PricingModelType = PricingModelType.FIXED_PER_CALL,
) -> int:
    async with db_session_factory.begin() as session:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key=key,
            name=f"{key} name",
            summary=f"{key} summary",
            description=f"{key} description",
            access_mode=access_mode,
            request_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            response_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            timeout_seconds=30,
            is_enabled=is_enabled,
        )
        session.add(endpoint)
        await session.flush()
        session.add(
            PricingModel(
                endpoint_id=endpoint.id,
                pricing_type=pricing_type,
                amount_minor=None if pricing_type is PricingModelType.FREE else 500,
                currency=None if pricing_type is PricingModelType.FREE else "USD",
            )
        )
        return endpoint.id


@pytest.mark.asyncio
async def test_create_quote_returns_snapshot_for_active_public_endpoint(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )

    before_request = datetime.now(UTC)
    response = await async_client.post(
        "/v1/services/quote-service/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello", "target": "fr"}},
    )
    after_request = datetime.now(UTC)

    assert response.status_code == 201
    body = response.json()
    assert body["service_id"] == service_id
    assert body["endpoint_key"] == "translate"
    assert body["pricing_type"] == "fixed_per_call"
    assert body["amount_minor"] == 500
    assert body["currency"] == "USD"
    assert (
        body["request_hash"] == "14e80198f61e15ffefaa9ca542d2611bb2fb67d592c1f1d177b730b3410dc102"
    )
    assert body["service_revision_id"] is not None
    assert body["service_change_token"] == "a" * 64

    expires_at = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    assert (
        before_request + timedelta(minutes=5) <= expires_at <= after_request + timedelta(minutes=5)
    )


@pytest.mark.asyncio
async def test_create_quote_returns_free_pricing_snapshot_for_free_endpoint(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="free-quote-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.FREE,
        pricing_type=PricingModelType.FREE,
    )

    response = await async_client.post(
        f"/v1/services/{service_id}/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 201
    assert response.json()["pricing_type"] == "free"
    assert response.json()["amount_minor"] is None
    assert response.json()["currency"] is None


@pytest.mark.asyncio
async def test_create_quote_returns_not_found_for_unknown_service(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/v1/services/does-not-exist/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "service not found"}


@pytest.mark.asyncio
async def test_create_quote_returns_not_found_for_missing_endpoint(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
    )

    response = await async_client.post(
        "/v1/services/quote-service/quote",
        json={"endpoint_key": "missing", "payload": {"text": "hello"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "endpoint not found"}


@pytest.mark.asyncio
async def test_create_quote_returns_not_found_for_disabled_endpoint(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
        is_enabled=False,
    )

    response = await async_client.post(
        "/v1/services/quote-service/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "endpoint not found"}


@pytest.mark.asyncio
async def test_create_quote_rejects_non_object_payload(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )

    response = await async_client.post(
        "/v1/services/quote-service/quote",
        json={"endpoint_key": "translate", "payload": ["not", "an", "object"]},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_quote_persists_quote_record(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )

    response = await async_client.post(
        "/v1/services/quote-service/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 201
    quote_id = response.json()["id"]

    async with db_session_factory() as session:
        quote = await session.get(Quote, quote_id)

    assert quote is not None
    assert quote.service_id == service_id
    assert quote.endpoint_id == endpoint_id
    assert quote.endpoint_key == "translate"
    assert quote.expires_at > quote.created_at
    ttl_delta = quote.expires_at - quote.created_at
    assert timedelta(minutes=4, seconds=59) <= ttl_delta <= timedelta(minutes=5, seconds=1)


@pytest.mark.asyncio
async def test_create_quote_works_with_authenticated_consumer(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="auth-quote-service",
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )

    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id

    response = await async_client.post(
        f"/v1/services/{service_id}/quote",
        headers={"X-Account-Id": str(account_id)},
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_quote_returns_not_found_for_draft_service(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="draft-quote-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        key="translate",
    )

    response = await async_client.post(
        f"/v1/services/{service_id}/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "service not found"}
