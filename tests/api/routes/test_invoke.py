from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from httpx import AsyncClient, Response, TimeoutException
from tests.fixtures.domain import (
    ConsumerAccountFactory,
    EndpointFactory,
    ProviderAccountFactory,
    ServiceFactory,
    create_consumer_account_record,
    create_endpoint_price_record,
    create_endpoint_record,
    create_moderation_action_record,
    create_payment_attempt_record,
    create_provider_account_record,
    create_quote_record,
    create_service_record,
    create_upstream_record,
)
from tests.helpers.auth import auth_headers_for_account_id

from app.core.enums import (
    AccessMode,
    InvocationStatus,
    PaymentAttemptStatus,
    PricingModelType,
    ServiceLifecycle,
)
from app.core.lifespan import get_app_state
from app.core.request_hash import hash_request_body
from app.core.security import hash_api_key
from app.db.models import ApiKey, Invocation, Quote

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _auth_headers(account_id: int) -> dict[str, str]:
    return auth_headers_for_account_id(account_id, idempotency_key="invoke-key")


def _api_key_headers(api_key: str, *, idempotency_key: str = "invoke-key") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Idempotency-Key": idempotency_key,
    }


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    return await create_provider_account_record(
        db_session_factory,
        display_name="Provider",
    )


async def _create_consumer_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    return await create_consumer_account_record(
        db_session_factory,
        display_name="Consumer",
    )


async def _seed_api_key(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
    plaintext: str = "amp_invoke-test-key",
) -> str:
    async with db_session_factory.begin() as session:
        session.add(
            ApiKey(
                account_id=account_id,
                name="invoke-key",
                key_prefix=plaintext[:16],
                key_hash=hash_api_key(plaintext),
            )
        )
    return plaintext


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str = "invoke-service",
    lifecycle: ServiceLifecycle = ServiceLifecycle.ACTIVE,
    with_revision: bool = True,
) -> int:
    return await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        name="Invoke Service",
        summary="Invoke summary",
        description=None,
        lifecycle=lifecycle,
        with_revision=with_revision,
    )


async def _seed_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    access_mode: AccessMode = AccessMode.FREE,
    is_enabled: bool = True,
    with_hmac_auth: bool = True,
    request_schema: dict[str, object] | None = None,
) -> int:
    default_request_schema: dict[str, object] = {"type": "object"}
    resolved_request_schema = (
        request_schema if request_schema is not None else default_request_schema
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        name="Translate",
        summary="Translate text",
        description=None,
        access_mode=access_mode,
        request_schema=resolved_request_schema,
        response_schema={"type": "object"},
        is_enabled=is_enabled,
    )
    upstream_config: dict[str, Any] = {}
    if with_hmac_auth:
        upstream_config = {
            "auth": {
                "type": "hmac_sha256",
                "key_id": "gateway-key",
                "secret": "super-secret",
            },
        }
    await create_upstream_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        config=upstream_config,
    )
    if access_mode is AccessMode.PAID:
        await create_endpoint_price_record(
            db_session_factory,
            endpoint_id=endpoint_id,
            amount_minor=500,
            currency="USD",
        )
    return endpoint_id


async def _seed_quote(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    endpoint_id: int,
    payload: dict[str, object] | None = None,
) -> int:
    return await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload=payload,
        pricing_type=PricingModelType.FREE,
        amount_minor=None,
        currency=None,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )


async def _seed_existing_invocation(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    consumer_account_id: int,
    service_id: int,
    endpoint_id: int,
    endpoint_key: str,
    access_mode: AccessMode,
    quote_id: int | None,
    idempotency_key: str,
    payload: dict[str, object],
    status: InvocationStatus,
    response_payload: dict[str, object] | None,
    upstream_status_code: int | None,
    error_message: str | None,
) -> int:
    async with db_session_factory.begin() as session:
        invocation = Invocation(
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key=endpoint_key,
            access_mode=access_mode,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            request_hash=hash_request_body(
                {
                    "service_id": service_id,
                    "endpoint_key": endpoint_key,
                    "payload": payload,
                    "quote_id": quote_id,
                }
            ),
            status=status,
            response_payload=response_payload,
            upstream_status_code=upstream_status_code,
            error_message=error_message,
        )
        session.add(invocation)
        await session.flush()
        return invocation.id


async def _seed_moderation_action(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    action: str,
) -> None:
    await create_moderation_action_record(
        db_session_factory,
        service_id=service_id,
        action=action,
    )


async def _expire_quote(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    quote_id: int,
) -> None:
    async with db_session_factory.begin() as session:
        quote = await session.get(Quote, quote_id)
        assert quote is not None
        quote.expires_at = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass
class _FakeHttpClient:
    responses: list[Response]
    calls: list[dict[str, Any]]

    def __init__(self, responses: list[Response] | None = None) -> None:
        self.responses = [] if responses is None else responses
        self.calls = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        **kwargs: int,
    ) -> Response:
        timeout = kwargs["timeout"]
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            },
        )
        if not self.responses:
            raise AssertionError("no fake response configured")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_invoke_requires_auth_header(async_client: AsyncClient) -> None:
    response = await async_client.post(
        "/v1/invoke/invoke-service",
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Authorization header is required"}


@pytest.mark.asyncio
async def test_invoke_requires_idempotency_key(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers={"Authorization": _auth_headers(consumer_account_id)["Authorization"]},
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invoke_rejects_blank_idempotency_key_after_trimming(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    consumer_account_factory: ConsumerAccountFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="invoke-service",
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )
    await endpoint_factory(
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.FREE,
    )
    consumer_account_id = await consumer_account_factory()

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers={
            **auth_headers_for_account_id(consumer_account_id),
            "Idempotency-Key": "   ",
        },
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invoke_rejects_overlong_idempotency_key(
    async_client: AsyncClient,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    consumer_account_factory: ConsumerAccountFactory,
) -> None:
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="invoke-service",
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )
    await endpoint_factory(
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.FREE,
    )
    consumer_account_id = await consumer_account_factory()

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers={
            **auth_headers_for_account_id(consumer_account_id),
            "Idempotency-Key": "x" * 256,
        },
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_paid_endpoint_without_quote_returns_conflict(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    consumer_account_id = await _create_consumer_account(db_session_factory)

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "paid invoke requires quote"}


@pytest.mark.asyncio
async def test_free_invoke_returns_invocation_and_provider_result(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    fake_http_client = _FakeHttpClient(
        responses=[
            Response(
                status_code=200,
                json={"result": "bonjour"},
            ),
        ],
    )
    get_app_state(app).http_client = fake_http_client

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["service_id"] == service_id
    assert body["endpoint_key"] == "translate"
    assert body["access_mode"] == "free"
    assert body["status"] == "succeeded"
    assert body["upstream_status_code"] == 200
    assert body["response_payload"] == {"result": "bonjour"}
    assert body["error_message"] is None
    assert len(fake_http_client.calls) == 1


@pytest.mark.asyncio
async def test_invoke_and_invocation_reads_accept_api_key_bearer(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    api_key = await _seed_api_key(db_session_factory, account_id=consumer_account_id)
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
    )
    get_app_state(app).http_client = fake_http_client

    invoke_response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_api_key_headers(api_key),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert invoke_response.status_code == 200
    invocation_id = invoke_response.json()["id"]

    detail_response = await async_client.get(
        f"/v1/invocations/{invocation_id}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    list_response = await async_client.get(
        "/v1/invocations",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == invocation_id
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [invocation_id]
    assert len(fake_http_client.calls) == 1


@pytest.mark.asyncio
async def test_invoke_rejects_payload_that_does_not_match_endpoint_schema(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        request_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    consumer_account_id = await _create_consumer_account(db_session_factory)
    get_app_state(app).http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
    )

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": 123}},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "request payload does not match endpoint schema"}


@pytest.mark.asyncio
async def test_invoke_accepts_payload_that_matches_endpoint_schema(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        request_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    consumer_account_id = await _create_consumer_account(db_session_factory)
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
    )
    get_app_state(app).http_client = fake_http_client

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert len(fake_http_client.calls) == 1


@pytest.mark.asyncio
async def test_invoke_accepts_non_object_payload_and_response_when_schema_allows_it(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        request_schema={"type": "array", "items": {"type": "string"}},
    )
    consumer_account_id = await _create_consumer_account(db_session_factory)
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json="bonjour")],
    )
    get_app_state(app).http_client = fake_http_client

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": ["hello", "fr"]},
    )

    assert response.status_code == 200
    assert response.json()["response_payload"] == "bonjour"
    assert len(fake_http_client.calls) == 1


@pytest.mark.asyncio
async def test_free_invoke_replays_same_invocation_without_second_upstream_call(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
    )
    get_app_state(app).http_client = fake_http_client

    first = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )
    second = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(fake_http_client.calls) == 1


@pytest.mark.asyncio
async def test_free_invoke_replays_before_delisted_service_validation(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
    )
    get_app_state(app).http_client = fake_http_client

    first = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )
    await _seed_moderation_action(
        db_session_factory,
        service_id=service_id,
        action="delist",
    )
    second = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(fake_http_client.calls) == 1


@pytest.mark.asyncio
async def test_free_invoke_replays_before_expired_quote_validation(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    quote_id = await _seed_quote(db_session_factory, service_id=service_id, endpoint_id=endpoint_id)
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
    )
    get_app_state(app).http_client = fake_http_client
    existing_invocation_id = await _seed_existing_invocation(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        endpoint_key="translate",
        access_mode=AccessMode.FREE,
        quote_id=quote_id,
        idempotency_key="invoke-key",
        payload={"text": "hello"},
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
        upstream_status_code=200,
        error_message=None,
    )
    await _expire_quote(db_session_factory, quote_id=quote_id)
    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert response.status_code == 200
    assert response.json()["id"] == existing_invocation_id
    assert len(fake_http_client.calls) == 0


@pytest.mark.asyncio
async def test_free_invoke_replays_success_before_delist_validation(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    invocation_id = await _seed_existing_invocation(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        endpoint_key="translate",
        access_mode=AccessMode.FREE,
        quote_id=None,
        idempotency_key="invoke-key",
        payload={"text": "hello"},
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "cached"},
        upstream_status_code=200,
        error_message=None,
    )
    await _seed_moderation_action(
        db_session_factory,
        service_id=service_id,
        action="delist",
    )
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "fresh"})],
    )
    get_app_state(app).http_client = fake_http_client

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 200
    assert response.json()["id"] == invocation_id
    assert response.json()["response_payload"] == {"result": "cached"}
    assert len(fake_http_client.calls) == 0


@pytest.mark.asyncio
async def test_paid_invoke_replays_success_before_quote_expiry_validation(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    consumer_account_id = await _create_consumer_account(db_session_factory)
    quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
    )
    invocation_id = await _seed_existing_invocation(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        endpoint_key="translate",
        access_mode=AccessMode.PAID,
        quote_id=quote_id,
        idempotency_key="invoke-key",
        payload={"text": "hello"},
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "cached"},
        upstream_status_code=200,
        error_message=None,
    )
    await create_payment_attempt_record(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        quote_id=quote_id,
        invocation_id=invocation_id,
        idempotency_key="invoke-key",
        payment_identifier="payment-replay",
        status=PaymentAttemptStatus.CONSUMED,
        settle_outcome={
            "success": True,
            "transaction": "0xsettle-replay",
            "network": "eip155:84532",
            "payer": "0xconsumer",
        },
    )
    await _expire_quote(db_session_factory, quote_id=quote_id)
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "fresh"})],
    )
    get_app_state(app).http_client = fake_http_client

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert response.status_code == 200
    assert response.json()["id"] == invocation_id
    assert response.json()["response_payload"] == {"result": "cached"}
    assert len(fake_http_client.calls) == 0


@pytest.mark.asyncio
async def test_free_invoke_recovers_from_duplicate_insert_and_replays_existing_invocation(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    existing_invocation_id = await _seed_existing_invocation(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        endpoint_key="translate",
        access_mode=AccessMode.FREE,
        quote_id=None,
        idempotency_key="invoke-key",
        payload={"text": "hello"},
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "cached"},
        upstream_status_code=200,
        error_message=None,
    )
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "fresh"})],
    )
    get_app_state(app).http_client = fake_http_client

    from app.repositories.invocation_repo import InvocationRepository

    original_get_by_idempotency_key = InvocationRepository.get_by_idempotency_key
    lookup_calls = 0

    async def stale_then_delegate(
        self: InvocationRepository,
        *,
        consumer_account_id: int,
        idempotency_key: str,
    ) -> Invocation | None:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return None
        return await original_get_by_idempotency_key(
            self,
            consumer_account_id=consumer_account_id,
            idempotency_key=idempotency_key,
        )

    monkeypatch.setattr(InvocationRepository, "get_by_idempotency_key", stale_then_delegate)

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 200
    assert response.json()["id"] == existing_invocation_id
    assert response.json()["response_payload"] == {"result": "cached"}
    assert len(fake_http_client.calls) == 0


@pytest.mark.asyncio
async def test_free_invoke_duplicate_insert_preserves_request_mismatch_conflict(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    await _seed_existing_invocation(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        endpoint_key="translate",
        access_mode=AccessMode.FREE,
        quote_id=None,
        idempotency_key="invoke-key",
        payload={"text": "hello"},
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "cached"},
        upstream_status_code=200,
        error_message=None,
    )
    get_app_state(app).http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "fresh"})],
    )

    from app.repositories.invocation_repo import InvocationRepository

    original_get_by_idempotency_key = InvocationRepository.get_by_idempotency_key
    lookup_calls = 0

    async def stale_then_delegate(
        self: InvocationRepository,
        *,
        consumer_account_id: int,
        idempotency_key: str,
    ) -> Invocation | None:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return None
        return await original_get_by_idempotency_key(
            self,
            consumer_account_id=consumer_account_id,
            idempotency_key=idempotency_key,
        )

    monkeypatch.setattr(InvocationRepository, "get_by_idempotency_key", stale_then_delegate)

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hi"}},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "idempotency key already used for a different request"}


@pytest.mark.asyncio
async def test_free_invoke_rejects_reused_key_for_different_request(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    fake_http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
    )
    get_app_state(app).http_client = fake_http_client

    first = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )
    second = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hi"}},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {"detail": "idempotency key already used for a different request"}
    assert len(fake_http_client.calls) == 1


@pytest.mark.asyncio
async def test_free_invoke_replays_successful_invocation_after_service_is_delisted(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    invocation_id = await _seed_existing_invocation(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        endpoint_key="translate",
        access_mode=AccessMode.FREE,
        quote_id=None,
        idempotency_key="invoke-key",
        payload={"text": "hello"},
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "cached"},
        upstream_status_code=200,
        error_message=None,
    )
    await _seed_moderation_action(
        db_session_factory,
        service_id=service_id,
        action="delist",
    )
    fake_http_client = _FakeHttpClient()
    get_app_state(app).http_client = fake_http_client

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 200
    assert response.json()["id"] == invocation_id
    assert response.json()["response_payload"] == {"result": "cached"}
    assert len(fake_http_client.calls) == 0


@pytest.mark.asyncio
async def test_free_invoke_replays_successful_invocation_after_quote_expires(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    quote_id = await _seed_quote(db_session_factory, service_id=service_id, endpoint_id=endpoint_id)
    invocation_id = await _seed_existing_invocation(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        endpoint_key="translate",
        access_mode=AccessMode.FREE,
        quote_id=quote_id,
        idempotency_key="invoke-key",
        payload={"text": "hello"},
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "cached"},
        upstream_status_code=200,
        error_message=None,
    )
    await _expire_quote(db_session_factory, quote_id=quote_id)
    fake_http_client = _FakeHttpClient()
    get_app_state(app).http_client = fake_http_client

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert response.status_code == 200
    assert response.json()["id"] == invocation_id
    assert response.json()["response_payload"] == {"result": "cached"}
    assert len(fake_http_client.calls) == 0


@pytest.mark.asyncio
async def test_invoke_list_and_detail_are_consumer_scoped(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_one = await _create_consumer_account(db_session_factory)
    consumer_two = await _create_consumer_account(db_session_factory)
    get_app_state(app).http_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "one"})],
    )

    invoke = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_one),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )
    invocation_id = invoke.json()["id"]

    owned_list = await async_client.get(
        "/v1/invocations",
        headers=_auth_headers(consumer_one),
    )
    foreign_detail = await async_client.get(
        f"/v1/invocations/{invocation_id}",
        headers=_auth_headers(consumer_two),
    )

    assert owned_list.status_code == 200
    assert [item["id"] for item in owned_list.json()] == [invocation_id]
    assert foreign_detail.status_code == 404
    assert foreign_detail.json() == {"detail": "invocation not found"}


@pytest.mark.asyncio
async def test_invoke_maps_upstream_timeout_to_504(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)

    class _TimeoutingClient:
        async def request(
            self,
            method: str,
            url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            **kwargs: int,
        ) -> Response:
            _ = (method, url, json, headers, kwargs)
            raise TimeoutException("boom")

        async def aclose(self) -> None:
            return None

    get_app_state(app).http_client = _TimeoutingClient()

    response = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 504


@pytest.mark.asyncio
async def test_failed_invoke_replays_original_gateway_error_without_second_upstream_call(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_endpoint(db_session_factory, service_id=service_id)
    consumer_account_id = await _create_consumer_account(db_session_factory)

    class _TimeoutingClient:
        def __init__(self) -> None:
            self.calls = 0

        async def request(
            self,
            method: str,
            url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            **kwargs: int,
        ) -> Response:
            _ = (method, url, json, headers, kwargs)
            self.calls += 1
            raise TimeoutException("boom")

        async def aclose(self) -> None:
            return None

    fake_http_client = _TimeoutingClient()
    get_app_state(app).http_client = fake_http_client

    first = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )
    second = await async_client.post(
        "/v1/invoke/invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert first.status_code == 504
    assert second.status_code == 504
    assert second.json() == {"detail": "upstream request timed out"}
    assert fake_http_client.calls == 1
