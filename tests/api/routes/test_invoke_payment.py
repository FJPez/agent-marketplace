from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from x402 import PaymentPayload
from x402.http import encode_payment_signature_header

from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.core.lifespan import get_app_state
from app.core.request_hash import hash_request_body
from app.db.models import (
    Account,
    ConsumerProfile,
    Invocation,
    PaymentAttempt,
    PricingModel,
    ProviderProfile,
    ProviderUpstream,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _auth_headers(
    account_id: int,
    *,
    idempotency_key: str = "invoke-key",
    payment_header: str | None = None,
) -> dict[str, str]:
    headers = {"X-Account-Id": str(account_id), "Idempotency-Key": idempotency_key}
    if payment_header is not None:
        headers["X-PAYMENT"] = payment_header
    return headers


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(ProviderProfile(account_id=account.id, display_name="Provider"))
        return account.id


async def _create_consumer_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(ConsumerProfile(account_id=account.id, display_name="Consumer"))
        return account.id


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str = "paid-invoke-service",
) -> int:
    async with db_session_factory.begin() as session:
        service = Service(
            provider_account_id=provider_account_id,
            slug=slug,
            name="Paid Invoke Service",
            summary="Invoke summary",
            description=None,
            lifecycle=ServiceLifecycle.ACTIVE,
        )
        session.add(service)
        await session.flush()
        revision = ServiceRevision(
            service_id=service.id,
            revision_number=1,
            change_token="c" * 64,
            snapshot={"slug": slug},
        )
        session.add(revision)
        await session.flush()
        service.current_revision_id = revision.id
        service.current_change_token = revision.change_token
        return service.id


async def _seed_paid_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    currency: str = "USD",
) -> int:
    async with db_session_factory.begin() as session:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key="translate",
            name="Translate",
            summary="Translate text",
            description=None,
            access_mode=AccessMode.PAID,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()
        session.add(
            ProviderUpstream(
                endpoint_id=endpoint.id,
                base_url="https://provider.internal",
                path="/invoke",
                http_method="POST",
                config={
                    "auth": {
                        "type": "hmac_sha256",
                        "key_id": "gateway-key",
                        "secret": "super-secret",
                    },
                },
            )
        )
        session.add(
            PricingModel(
                endpoint_id=endpoint.id,
                pricing_type=PricingModelType.FIXED_PER_CALL,
                amount_minor=500,
                currency=currency,
            )
        )
        return endpoint.id


async def _seed_quote(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    endpoint_id: int,
    payload: dict[str, object],
    amount_minor: int = 500,
    currency: str = "USD",
) -> int:
    async with db_session_factory.begin() as session:
        quote = Quote(
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key="translate",
            request_hash=hash_request_body(payload),
            pricing_type=PricingModelType.FIXED_PER_CALL,
            amount_minor=amount_minor,
            currency=currency,
            service_revision_id=1,
            service_change_token="c" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(quote)
        await session.flush()
        return quote.id


async def _count_rows(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    async with db_session_factory() as session:
        invocation_count = await session.scalar(select(func.count()).select_from(Invocation))
        payment_attempt_count = await session.scalar(
            select(func.count()).select_from(PaymentAttempt)
        )
    return invocation_count or 0, payment_attempt_count or 0


@dataclass
class _FakeHttpClient:
    responses: list[Response] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        **kwargs: object,
    ) -> Response:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
                "kwargs": kwargs,
            }
        )
        if not self.responses:
            raise AssertionError("no fake upstream response configured")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return None


@dataclass
class _FakeFacilitatorClient:
    verify_outcomes: list[dict[str, object]]
    settle_outcomes: list[dict[str, object]]
    verify_calls: list[dict[str, object]] = field(default_factory=list)
    settle_calls: list[dict[str, object]] = field(default_factory=list)

    async def verify(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        self.verify_calls.append(
            {
                "payment_requirement": payment_requirement,
                "payment_payload": payment_payload,
            }
        )
        if not self.verify_outcomes:
            raise AssertionError("no fake verify outcome configured")
        return self.verify_outcomes.pop(0)

    async def settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        self.settle_calls.append(
            {
                "payment_requirement": payment_requirement,
                "payment_payload": payment_payload,
            }
        )
        if not self.settle_outcomes:
            raise AssertionError("no fake settle outcome configured")
        return self.settle_outcomes.pop(0)


class _FakeX402ResourceServer:
    def build_payment_required_headers(
        self,
        *,
        payment_requirement: dict[str, object],
    ) -> dict[str, str]:
        return {"X-PAYMENT-REQUIRED": json.dumps(payment_requirement, sort_keys=True)}

    def build_payment_response_headers(
        self,
        *,
        settle_outcome: dict[str, object],
    ) -> dict[str, str]:
        return {"X-PAYMENT-RESPONSE": json.dumps(settle_outcome, sort_keys=True)}


def _payment_header(*, payment_identifier: str) -> str:
    return encode_payment_signature_header(
        PaymentPayload.model_validate(
            {
                "payload": {
                    "authorization": {"nonce": payment_identifier},
                    "transaction": "0xabc123",
                },
                "accepted": {
                    "scheme": "exact",
                    "network": "eip155:84532",
                    "asset": "usdc",
                    "amount": "500",
                    "payTo": "0x000000000000000000000000000000000000c0de",
                    "maxTimeoutSeconds": 300,
                    "extra": {},
                },
            }
        )
    )


def _install_payment_state(
    app: FastAPI,
    *,
    upstream_client: _FakeHttpClient,
    facilitator_client: _FakeFacilitatorClient,
) -> None:
    state = get_app_state(app)
    state.http_client = upstream_client
    state.facilitator_client = facilitator_client
    state.x402_resource_server = _FakeX402ResourceServer()
    state.settings.x402_pay_to_address = "0x000000000000000000000000000000000000c0de"


@pytest.mark.asyncio
async def test_paid_invoke_without_payment_returns_402_and_creates_no_records(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_paid_endpoint(db_session_factory, service_id=service_id)
    quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    _install_payment_state(
        app,
        upstream_client=_FakeHttpClient(),
        facilitator_client=_FakeFacilitatorClient(verify_outcomes=[], settle_outcomes=[]),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(consumer_account_id),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    invocation_count, payment_attempt_count = await _count_rows(db_session_factory)

    assert response.status_code == 402
    assert response.json() == {"detail": "payment required"}
    assert "X-PAYMENT-REQUIRED" in response.headers
    assert invocation_count == 0
    assert payment_attempt_count == 0


@pytest.mark.asyncio
async def test_paid_invoke_with_valid_payment_returns_success_and_payment_response_header(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_paid_endpoint(db_session_factory, service_id=service_id)
    quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    upstream_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})]
    )
    facilitator_client = _FakeFacilitatorClient(
        verify_outcomes=[{"ok": True, "reference": "verify-1"}],
        settle_outcomes=[{"ok": True, "reference": "settle-1"}],
    )
    _install_payment_state(
        app,
        upstream_client=upstream_client,
        facilitator_client=facilitator_client,
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["response_payload"] == {"result": "bonjour"}
    assert "X-PAYMENT-RESPONSE" in response.headers
    assert len(upstream_client.calls) == 1
    assert len(facilitator_client.verify_calls) == 1
    assert len(facilitator_client.settle_calls) == 1


@pytest.mark.asyncio
async def test_successful_paid_invoke_replays_by_idempotency_key_without_second_upstream_call(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_paid_endpoint(db_session_factory, service_id=service_id)
    quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    upstream_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})]
    )
    facilitator_client = _FakeFacilitatorClient(
        verify_outcomes=[{"ok": True, "reference": "verify-1"}],
        settle_outcomes=[{"ok": True, "reference": "settle-1"}],
    )
    _install_payment_state(
        app,
        upstream_client=upstream_client,
        facilitator_client=facilitator_client,
    )
    headers = _auth_headers(
        consumer_account_id,
        payment_header=_payment_header(payment_identifier="payment-1"),
    )

    first = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=headers,
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )
    second = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=headers,
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(upstream_client.calls) == 1


@pytest.mark.asyncio
async def test_successful_paid_invoke_replays_by_payment_identifier_without_second_upstream_call(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_paid_endpoint(db_session_factory, service_id=service_id)
    quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    upstream_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})]
    )
    facilitator_client = _FakeFacilitatorClient(
        verify_outcomes=[{"ok": True, "reference": "verify-1"}],
        settle_outcomes=[{"ok": True, "reference": "settle-1"}],
    )
    _install_payment_state(
        app,
        upstream_client=upstream_client,
        facilitator_client=facilitator_client,
    )

    first = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-1",
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )
    second = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-2",
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(upstream_client.calls) == 1


@pytest.mark.asyncio
async def test_failed_payment_identifier_reuse_returns_conflict(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_paid_endpoint(db_session_factory, service_id=service_id)
    quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    _install_payment_state(
        app,
        upstream_client=_FakeHttpClient(),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[{"ok": False, "reference": "verify-1"}],
            settle_outcomes=[],
        ),
    )

    first = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-1",
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )
    second = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-2",
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert first.status_code == 402
    assert second.status_code == 409
    assert second.json() == {"detail": "payment identifier already used"}


@pytest.mark.asyncio
async def test_verify_failure_returns_402(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_paid_endpoint(db_session_factory, service_id=service_id)
    quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    _install_payment_state(
        app,
        upstream_client=_FakeHttpClient(),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[{"ok": False, "reference": "verify-1"}],
            settle_outcomes=[],
        ),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert response.status_code == 402
    assert response.json() == {"detail": "payment could not be verified"}
    assert "X-PAYMENT-REQUIRED" in response.headers


@pytest.mark.asyncio
async def test_settle_failure_returns_502(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_paid_endpoint(db_session_factory, service_id=service_id)
    quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    _install_payment_state(
        app,
        upstream_client=_FakeHttpClient(),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[{"ok": True, "reference": "verify-1"}],
            settle_outcomes=[{"ok": False, "reference": "settle-1"}],
        ),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "payment settlement failed"}


@pytest.mark.asyncio
async def test_paid_invoke_requires_quote(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    _ = await _seed_paid_endpoint(db_session_factory, service_id=service_id)
    _install_payment_state(
        app,
        upstream_client=_FakeHttpClient(),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[],
            settle_outcomes=[],
        ),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "paid invoke requires quote"}
