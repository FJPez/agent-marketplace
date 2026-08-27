import logging
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    ConsumerAccountFactory,
    EndpointFactory,
    EndpointPriceFactory,
    ModerationActionFactory,
    ProviderAccountFactory,
    ServiceFactory,
)
from tests.helpers.auth import (
    auth_headers_for_account,
    auth_headers_for_account_id,
    wallet_address_for_index,
)

from app.core.config import get_settings
from app.core.enums import AccessMode, ServiceLifecycle
from app.core.logging import EVENT_FIELD, QUOTE_ID_FIELD, REQUEST_ID_FIELD, SERVICE_ID_FIELD
from app.db.models import Account, Quote
from app.main import create_app


async def _create_provider_account(
    provider_account_factory: ProviderAccountFactory,
) -> int:
    return await provider_account_factory()


async def _seed_service(
    service_factory: ServiceFactory,
    *,
    provider_account_id: int,
    slug: str,
    lifecycle: ServiceLifecycle = ServiceLifecycle.ACTIVE,
    with_revision: bool = False,
) -> int:
    return await service_factory(
        provider_account_id=provider_account_id,
        slug=slug,
        lifecycle=lifecycle,
        with_revision=with_revision,
        change_token="a" * 64,
    )


async def _seed_endpoint(
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
    *,
    service_id: int,
    key: str,
    access_mode: AccessMode = AccessMode.PAID,
    is_enabled: bool = True,
    with_price: bool = True,
) -> int:
    endpoint_id = await endpoint_factory(
        service_id=service_id,
        key=key,
        access_mode=access_mode,
        is_enabled=is_enabled,
        request_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        response_schema={"type": "object", "properties": {"result": {"type": "string"}}},
    )
    if with_price:
        await endpoint_price_factory(
            endpoint_id=endpoint_id,
            amount_minor=500,
            currency="USD",
        )
    return endpoint_id


async def _seed_moderation_action(
    moderation_action_factory: ModerationActionFactory,
    *,
    service_id: int,
    action: str,
) -> None:
    await moderation_action_factory(service_id=service_id, action=action)


@pytest.mark.asyncio
async def test_create_quote_returns_snapshot_for_active_public_endpoint(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
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
async def test_create_quote_logs_correlated_quote_event(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="quote-log-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
    )

    with caplog.at_level(logging.INFO, logger="app.services.quotes"):
        response = await async_client.post(
            "/v1/services/quote-log-service/quote",
            headers={"X-Request-ID": "quote-req-1"},
            json={"endpoint_key": "translate", "payload": {"text": "hello"}},
        )

    assert response.status_code == 201
    record = next(
        record
        for record in caplog.records
        if record.name == "app.services.quotes"
        and getattr(record, EVENT_FIELD, None) == "quote.created"
    )
    assert getattr(record, EVENT_FIELD) == "quote.created"
    assert getattr(record, REQUEST_ID_FIELD) == "quote-req-1"
    assert getattr(record, SERVICE_ID_FIELD) == service_id
    assert getattr(record, QUOTE_ID_FIELD) == response.json()["id"]


@pytest.mark.asyncio
async def test_create_quote_returns_conflict_for_free_endpoint(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="free-quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.FREE,
        with_price=False,
    )

    response = await async_client.post(
        f"/v1/services/{service_id}/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "endpoint is not quoteable"}


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
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
        with_revision=True,
    )

    response = await async_client.post(
        "/v1/services/quote-service/quote",
        json={"endpoint_key": "missing", "payload": {"text": "hello"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "endpoint not found"}


@pytest.mark.asyncio
async def test_create_quote_rate_limits_repeated_requests(
    migrated_database: None,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = migrated_database
    monkeypatch.setenv("APP_API_RATE_LIMIT", "10/minute")
    monkeypatch.setenv("APP_QUOTE_RATE_LIMIT", "1/minute")
    monkeypatch.setenv("APP_INVOKE_RATE_LIMIT", "10/minute")

    get_settings.cache_clear()
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
    )

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as rate_limited_client:
            first = await rate_limited_client.post(
                "/v1/services/quote-service/quote",
                json={"endpoint_key": "translate", "payload": {"text": "hello"}},
            )
            second = await rate_limited_client.post(
                "/v1/services/quote-service/quote",
                json={"endpoint_key": "translate", "payload": {"text": "hello"}},
            )
    get_settings.cache_clear()

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json() == {"detail": "rate limit exceeded"}


@pytest.mark.asyncio
async def test_create_quote_rate_limit_scopes_authenticated_accounts_separately(
    migrated_database: None,
    provider_account_factory: ProviderAccountFactory,
    consumer_account_factory: ConsumerAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = migrated_database
    monkeypatch.setenv("APP_API_RATE_LIMIT", "10/minute")
    monkeypatch.setenv("APP_QUOTE_RATE_LIMIT", "1/minute")
    monkeypatch.setenv("APP_INVOKE_RATE_LIMIT", "10/minute")

    get_settings.cache_clear()
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
    )
    first_consumer_account_id = await consumer_account_factory()
    second_consumer_account_id = await consumer_account_factory()

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as rate_limited_client:
            first = await rate_limited_client.post(
                "/v1/services/quote-service/quote",
                headers=await auth_headers_for_account(
                    db_session_factory,
                    account_id=first_consumer_account_id,
                ),
                json={"endpoint_key": "translate", "payload": {"text": "hello"}},
            )
            second = await rate_limited_client.post(
                "/v1/services/quote-service/quote",
                headers=await auth_headers_for_account(
                    db_session_factory,
                    account_id=second_consumer_account_id,
                ),
                json={"endpoint_key": "translate", "payload": {"text": "hello"}},
            )
    get_settings.cache_clear()

    assert first.status_code == 201
    assert second.status_code == 201


@pytest.mark.asyncio
async def test_create_quote_returns_not_found_for_disabled_endpoint(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
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
async def test_create_quote_accepts_non_object_payload_when_endpoint_schema_allows_it(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await endpoint_factory(
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
        request_schema={"type": "array", "items": {"type": "string"}},
        response_schema={"type": "string"},
    )
    await endpoint_price_factory(
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency="USD",
    )

    response = await async_client.post(
        "/v1/services/quote-service/quote",
        json={"endpoint_key": "translate", "payload": ["not", "an", "object"]},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_quote_rejects_payload_that_does_not_match_endpoint_schema(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="quote-schema-service",
        with_revision=True,
    )
    endpoint_id = await endpoint_factory(
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
        request_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        response_schema={"type": "object"},
    )
    await endpoint_price_factory(
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency="USD",
    )

    response = await async_client.post(
        "/v1/services/quote-schema-service/quote",
        json={"endpoint_key": "translate", "payload": {"text": 123}},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "request payload does not match endpoint schema"}


@pytest.mark.asyncio
async def test_create_quote_returns_conflict_for_paid_endpoint_without_fixed_pricing(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="unpriced-quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
        with_price=False,
    )

    response = await async_client.post(
        "/v1/services/unpriced-quote-service/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "endpoint is not quoteable"}


@pytest.mark.asyncio
async def test_create_quote_persists_quote_record(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
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
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="auth-quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
    )

    async with db_session_factory.begin() as session:
        account = Account(wallet_address=wallet_address_for_index(999))
        session.add(account)
        await session.flush()
        account_id = account.id

    response = await async_client.post(
        f"/v1/services/{service_id}/quote",
        headers=auth_headers_for_account_id(account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_quote_returns_not_found_for_draft_service(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="draft-quote-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
    )

    response = await async_client.post(
        f"/v1/services/{service_id}/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "service not found"}


@pytest.mark.asyncio
async def test_create_quote_returns_not_found_for_suspended_service(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
    moderation_action_factory: ModerationActionFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="suspended-quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
    )
    await _seed_moderation_action(
        moderation_action_factory,
        service_id=service_id,
        action="suspend",
    )

    response = await async_client.post(
        f"/v1/services/{service_id}/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "service not found"}


@pytest.mark.asyncio
async def test_create_quote_returns_not_found_for_delisted_service(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
    moderation_action_factory: ModerationActionFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="delisted-quote-service",
        with_revision=True,
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
    )
    await _seed_moderation_action(
        moderation_action_factory,
        service_id=service_id,
        action="delist",
    )

    response = await async_client.post(
        f"/v1/services/{service_id}/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "service not found"}


@pytest.mark.asyncio
async def test_create_quote_returns_conflict_when_service_lacks_contract_binding(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
) -> None:
    provider_account_id = await _create_provider_account(provider_account_factory)
    service_id = await _seed_service(
        service_factory,
        provider_account_id=provider_account_id,
        slug="unbound-quote-service",
    )
    await _seed_endpoint(
        endpoint_factory,
        endpoint_price_factory,
        service_id=service_id,
        key="translate",
    )

    response = await async_client.post(
        f"/v1/services/{service_id}/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "service contract is not quoteable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifier",
    ["Quote-Service", "quote service", "not_a_slug", "0"],
)
async def test_create_quote_rejects_malformed_service_identifiers(
    async_client: AsyncClient,
    identifier: str,
) -> None:
    response = await async_client.post(
        f"/v1/services/{identifier}/quote",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert all("service_id_or_slug" in error["loc"] for error in detail)
