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

from app.core.enums import AccessMode, InvocationStatus, PricingModelType, ServiceLifecycle
from app.core.lifespan import get_app_state
from app.core.request_hash import hash_request_body
from app.db.models import (
    Account,
    ConsumerProfile,
    Invocation,
    LedgerEntry,
    PaymentAttempt,
    PricingModel,
    ProviderProfile,
    ProviderUpstream,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)
from app.integrations.x402.facilitator_client import FacilitatorUnavailableError
from app.integrations.x402.resource_server import X402ResourceServerAdapter

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
        headers["PAYMENT-SIGNATURE"] = payment_header
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
) -> tuple[int, int, int]:
    async with db_session_factory() as session:
        invocation_count = await session.scalar(select(func.count()).select_from(Invocation))
        payment_attempt_count = await session.scalar(
            select(func.count()).select_from(PaymentAttempt)
        )
        ledger_count = await session.scalar(select(func.count()).select_from(LedgerEntry))
    return invocation_count or 0, payment_attempt_count or 0, ledger_count or 0


async def _get_payment_attempt(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    payment_identifier: str,
) -> PaymentAttempt | None:
    async with db_session_factory() as session:
        statement = select(PaymentAttempt).where(
            PaymentAttempt.payment_identifier == payment_identifier,
        )
        return await session.scalar(statement)


async def _seed_existing_invocation(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    consumer_account_id: int,
    service_id: int,
    endpoint_id: int,
    quote_id: int,
    idempotency_key: str,
    payload: dict[str, object],
    response_payload: dict[str, object],
) -> int:
    async with db_session_factory.begin() as session:
        invocation = Invocation(
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key="translate",
            access_mode=AccessMode.PAID,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            request_hash=hash_request_body(
                {
                    "service_id": service_id,
                    "endpoint_key": "translate",
                    "payload": payload,
                    "quote_id": quote_id,
                }
            ),
            status=InvocationStatus.SUCCEEDED,
            response_payload=response_payload,
            upstream_status_code=200,
            error_message=None,
        )
        session.add(invocation)
        await session.flush()
        return invocation.id


async def _seed_payment_attempt(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    consumer_account_id: int,
    quote_id: int,
    invocation_id: int | None,
    idempotency_key: str,
    payment_identifier: str,
    verify_outcome: dict[str, object] | None,
    settle_outcome: dict[str, object] | None,
) -> int:
    async with db_session_factory.begin() as session:
        attempt = PaymentAttempt(
            consumer_account_id=consumer_account_id,
            quote_id=quote_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            payment_identifier=payment_identifier,
            payment_requirement={"amount_minor": 500},
            payment_payload={"payment_identifier": payment_identifier},
            verify_outcome=verify_outcome,
            settle_outcome=settle_outcome,
            facilitator_reference="settle-1",
        )
        session.add(attempt)
        await session.flush()
        return attempt.id


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
        return {"PAYMENT-REQUIRED": json.dumps(payment_requirement, sort_keys=True)}

    def build_payment_response_headers(
        self,
        *,
        settle_outcome: dict[str, object],
    ) -> dict[str, str]:
        return {"PAYMENT-RESPONSE": json.dumps(settle_outcome, sort_keys=True)}


class _UnavailableFacilitatorClient:
    async def verify(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        _ = payment_requirement
        _ = payment_payload
        raise FacilitatorUnavailableError("facilitator unavailable")

    async def settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        _ = payment_requirement
        _ = payment_payload
        raise AssertionError("settle should not be called")


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
    facilitator_client: object,
    x402_resource_server: object | None = None,
) -> None:
    state = get_app_state(app)
    state.http_client = upstream_client
    state.facilitator_client = facilitator_client
    state.x402_resource_server = x402_resource_server or _FakeX402ResourceServer()
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

    invocation_count, payment_attempt_count, ledger_count = await _count_rows(db_session_factory)

    assert response.status_code == 402
    assert response.json() == {"detail": "payment required"}
    assert "PAYMENT-REQUIRED" in response.headers
    assert invocation_count == 0
    assert payment_attempt_count == 0
    assert ledger_count == 0


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
    assert "PAYMENT-RESPONSE" in response.headers
    assert len(upstream_client.calls) == 1
    assert len(facilitator_client.verify_calls) == 1
    assert len(facilitator_client.settle_calls) == 1


@pytest.mark.asyncio
async def test_successful_paid_invoke_writes_ledger_entries(
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
            payment_header=_payment_header(payment_identifier="payment-ledger"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert response.status_code == 200

    async with db_session_factory() as session:
        entries = list(
            (
                await session.execute(
                    select(LedgerEntry).order_by(LedgerEntry.id),
                )
            )
            .scalars()
            .all()
        )

    assert [entry.entry_type.value for entry in entries] == [
        "charge",
        "platform_fee",
        "provider_earning",
    ]
    assert [entry.amount_minor for entry in entries] == [500, 50, 450]


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
        verify_outcomes=[{"isValid": True, "reference": "verify-1"}],
        settle_outcomes=[
            {
                "success": True,
                "transaction": "0xsettled",
                "network": "eip155:84532",
                "payer": "0xpayer",
            }
        ],
    )
    _install_payment_state(
        app,
        upstream_client=upstream_client,
        facilitator_client=facilitator_client,
        x402_resource_server=X402ResourceServerAdapter(),
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
    async with db_session_factory.begin() as session:
        attempt = await session.scalar(
            select(PaymentAttempt).where(PaymentAttempt.payment_identifier == "payment-1"),
        )
        assert attempt is not None
        attempt.settle_outcome = {}
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
    assert "PAYMENT-RESPONSE" not in second.headers
    assert len(upstream_client.calls) == 1


@pytest.mark.asyncio
async def test_paid_invoke_duplicate_attempt_insert_returns_conflict_before_facilitator_calls(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
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
    await _seed_payment_attempt(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        quote_id=quote_id,
        invocation_id=None,
        idempotency_key="invoke-key-1",
        payment_identifier="payment-1",
        verify_outcome=None,
        settle_outcome=None,
    )
    upstream_client = _FakeHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
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

    from app.repositories.payment_attempt_repo import PaymentAttemptRepository

    original_get_by_payment_identifier = PaymentAttemptRepository.get_by_payment_identifier
    lookup_calls = 0

    async def stale_then_delegate(
        self: PaymentAttemptRepository,
        *,
        payment_identifier: str,
    ) -> PaymentAttempt | None:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return None
        return await original_get_by_payment_identifier(self, payment_identifier=payment_identifier)

    monkeypatch.setattr(
        PaymentAttemptRepository,
        "get_by_payment_identifier",
        stale_then_delegate,
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-2",
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "payment identifier already used"}
    assert len(facilitator_client.verify_calls) == 0
    assert len(facilitator_client.settle_calls) == 0
    assert len(upstream_client.calls) == 0


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
async def test_paid_invoke_rejects_payment_identifier_reuse_for_different_quote(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(db_session_factory, provider_account_id=provider_account_id)
    endpoint_id = await _seed_paid_endpoint(db_session_factory, service_id=service_id)
    first_quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    second_quote_id = await _seed_quote(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    invocation_id = await _seed_existing_invocation(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        quote_id=first_quote_id,
        idempotency_key="invoke-key-1",
        payload={"text": "hello"},
        response_payload={"result": "cached"},
    )
    await _seed_payment_attempt(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        quote_id=first_quote_id,
        invocation_id=invocation_id,
        idempotency_key="invoke-key-1",
        payment_identifier="payment-1",
        verify_outcome={"ok": True, "reference": "verify-1"},
        settle_outcome={"ok": True, "reference": "settle-1"},
    )
    _install_payment_state(
        app,
        upstream_client=_FakeHttpClient(
            responses=[Response(status_code=200, json={"result": "fresh"})],
        ),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[],
            settle_outcomes=[],
        ),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-2",
            payment_header=_payment_header(payment_identifier="payment-1"),
        ),
        json={
            "endpoint_key": "translate",
            "payload": {"text": "hello"},
            "quote_id": second_quote_id,
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "payment identifier already used"}


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
    assert "PAYMENT-REQUIRED" in response.headers


@pytest.mark.asyncio
async def test_paid_invoke_returns_502_when_facilitator_verify_raises(
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
        facilitator_client=_UnavailableFacilitatorClient(),
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
    assert response.json() == {"detail": "facilitator unavailable"}


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
