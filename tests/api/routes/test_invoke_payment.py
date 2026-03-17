from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from tests.helpers.auth import auth_headers_for_account_id
from x402 import PaymentPayload
from x402.http import encode_payment_signature_header

from app.core.enums import AccessMode, InvocationStatus, PricingModelType, ServiceLifecycle
from app.core.lifespan import get_app_state
from app.core.logging import (
    EVENT_FIELD,
    INVOCATION_ID_FIELD,
    PAYMENT_ATTEMPT_ID_FIELD,
    PAYOUT_ID_FIELD,
    PAYOUT_STATUS_FIELD,
    PROVIDER_ACCOUNT_ID_FIELD,
    REQUEST_ID_FIELD,
    SERVICE_ID_FIELD,
)
from app.core.request_hash import hash_request_body
from app.db.models import (
    Account,
    Invocation,
    LedgerEntry,
    PaymentAttempt,
    Payout,
    PricingModel,
    ProviderUpstream,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)
from app.integrations.payouts import PreparedPayout, SentPayout
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
    headers = auth_headers_for_account_id(account_id, idempotency_key=idempotency_key)
    if payment_header is not None:
        headers["PAYMENT-SIGNATURE"] = payment_header
    return headers


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    wallet_address: str = "0x00000000000000000000000000000000000000aa",
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(display_name="Provider", wallet_address=wallet_address)
        session.add(account)
        await session.flush()
        return account.id


async def _create_consumer_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(display_name="Consumer")
        session.add(account)
        await session.flush()
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
) -> tuple[int, int, int, int]:
    async with db_session_factory() as session:
        invocation_count = await session.scalar(select(func.count()).select_from(Invocation))
        payment_attempt_count = await session.scalar(
            select(func.count()).select_from(PaymentAttempt)
        )
        ledger_count = await session.scalar(select(func.count()).select_from(LedgerEntry))
        payout_count = await session.scalar(select(func.count()).select_from(Payout))
    return invocation_count or 0, payment_attempt_count or 0, ledger_count or 0, payout_count or 0


async def _list_payouts(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> list[Payout]:
    async with db_session_factory() as session:
        result = await session.scalars(select(Payout).order_by(Payout.id))
        return list(result.all())


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


async def _get_invocation_by_idempotency_key(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    idempotency_key: str,
) -> Invocation | None:
    async with db_session_factory() as session:
        statement = select(Invocation).where(Invocation.idempotency_key == idempotency_key)
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


class _SuccessfulPayoutExecutor:
    def __init__(self) -> None:
        self.prepare_calls: list[dict[str, object]] = []
        self.send_calls: list[dict[str, object]] = []

    async def prepare_payout(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
        idempotency_key: str,
        nonce: int,
    ) -> PreparedPayout:
        self.prepare_calls.append(
            {
                "destination_wallet": destination_wallet,
                "amount_minor": amount_minor,
                "idempotency_key": idempotency_key,
                "nonce": nonce,
            }
        )
        return PreparedPayout(
            raw_transaction="0xrawtx",
            reference="0xpayoutsent",
            network="base-sepolia",
            token_address="0x0000000000000000000000000000000000000001",
        )

    async def send_prepared_payout(
        self,
        *,
        raw_transaction: str,
        reference: str,
    ) -> SentPayout:
        self.send_calls.append(
            {
                "raw_transaction": raw_transaction,
                "reference": reference,
            }
        )
        return SentPayout(
            reference=reference,
            network="base-sepolia",
            token_address="0x0000000000000000000000000000000000000001",
        )


class _FailingPayoutExecutor:
    async def prepare_payout(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
        idempotency_key: str,
        nonce: int,
    ) -> PreparedPayout:
        _ = destination_wallet
        _ = amount_minor
        _ = idempotency_key
        _ = nonce
        raise RuntimeError("rpc unavailable")

    async def send_prepared_payout(
        self,
        *,
        raw_transaction: str,
        reference: str,
    ) -> SentPayout:
        _ = raw_transaction
        _ = reference
        raise RuntimeError("rpc unavailable")


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
    payout_executor: object | None = None,
    payouts_enabled: bool = False,
) -> None:
    state = get_app_state(app)
    state.http_client = upstream_client
    state.facilitator_client = facilitator_client
    state.x402_resource_server = x402_resource_server or _FakeX402ResourceServer()
    state.settings.x402_pay_to_address = "0x000000000000000000000000000000000000c0de"
    state.settings.payouts_enabled = payouts_enabled
    state.payout_executor = payout_executor


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

    invocation_count, payment_attempt_count, ledger_count, payout_count = await _count_rows(
        db_session_factory
    )

    assert response.status_code == 402
    assert response.json() == {"detail": "payment required"}
    assert "PAYMENT-REQUIRED" in response.headers
    assert invocation_count == 0
    assert payment_attempt_count == 0
    assert ledger_count == 0
    assert payout_count == 0


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
async def test_successful_paid_invoke_logs_invoke_and_ledger_events(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
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
        upstream_client=_FakeHttpClient(
            responses=[Response(status_code=200, json={"result": "bonjour"})]
        ),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[{"ok": True, "reference": "verify-1"}],
            settle_outcomes=[{"ok": True, "reference": "settle-1"}],
        ),
    )

    with caplog.at_level(logging.INFO):
        response = await async_client.post(
            "/v1/invoke/paid-invoke-service",
            headers={
                **_auth_headers(
                    consumer_account_id,
                    payment_header=_payment_header(payment_identifier="payment-log-success"),
                ),
                "X-Request-ID": "invoke-log-success",
            },
            json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
        )

    assert response.status_code == 200
    payment_attempt = await _get_payment_attempt(
        db_session_factory,
        payment_identifier="payment-log-success",
    )
    assert payment_attempt is not None

    invoke_record = next(
        record
        for record in caplog.records
        if record.name == "app.services.invoke_service"
        and getattr(record, EVENT_FIELD, None) == "invoke.succeeded"
    )
    ledger_record = next(
        record
        for record in caplog.records
        if record.name == "app.services.ledger_service"
        and getattr(record, EVENT_FIELD, None) == "ledger.recorded"
    )

    assert getattr(invoke_record, REQUEST_ID_FIELD) == "invoke-log-success"
    assert getattr(invoke_record, SERVICE_ID_FIELD) == service_id
    assert getattr(invoke_record, INVOCATION_ID_FIELD) == response.json()["id"]
    assert getattr(ledger_record, REQUEST_ID_FIELD) == "invoke-log-success"
    assert getattr(ledger_record, PROVIDER_ACCOUNT_ID_FIELD) == provider_account_id
    assert getattr(ledger_record, SERVICE_ID_FIELD) == service_id
    assert getattr(ledger_record, INVOCATION_ID_FIELD) == response.json()["id"]
    assert getattr(ledger_record, PAYMENT_ATTEMPT_ID_FIELD) == payment_attempt.id


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
async def test_paid_invoke_logs_failed_invoke_event_for_upstream_error(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
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
        upstream_client=_FakeHttpClient(
            responses=[Response(status_code=500, json={"detail": "upstream failed"})]
        ),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[{"ok": True, "reference": "verify-1"}],
            settle_outcomes=[{"ok": True, "reference": "settle-1"}],
        ),
    )

    with caplog.at_level(logging.ERROR):
        response = await async_client.post(
            "/v1/invoke/paid-invoke-service",
            headers={
                **_auth_headers(
                    consumer_account_id,
                    payment_header=_payment_header(payment_identifier="payment-log-failure"),
                ),
                "X-Request-ID": "invoke-log-failure",
            },
            json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "upstream request failed"}
    failed_invocation = await _get_invocation_by_idempotency_key(
        db_session_factory,
        idempotency_key="invoke-key",
    )
    assert failed_invocation is not None
    failure_record = next(
        record
        for record in caplog.records
        if record.name == "app.services.invoke_service"
        and getattr(record, EVENT_FIELD, None) == "invoke.failed"
    )
    assert getattr(failure_record, REQUEST_ID_FIELD) == "invoke-log-failure"
    assert getattr(failure_record, SERVICE_ID_FIELD) == service_id
    assert getattr(failure_record, INVOCATION_ID_FIELD) == failed_invocation.id


@pytest.mark.asyncio
async def test_successful_paid_invoke_records_ready_provider_payout_when_enabled(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
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
    payout_executor = _SuccessfulPayoutExecutor()
    _install_payment_state(
        app,
        upstream_client=_FakeHttpClient(
            responses=[Response(status_code=200, json={"result": "bonjour"})]
        ),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[{"ok": True, "reference": "verify-1"}],
            settle_outcomes=[{"ok": True, "reference": "settle-1"}],
        ),
        payout_executor=payout_executor,
        payouts_enabled=True,
    )

    with caplog.at_level(logging.INFO, logger="app.services.payout_service"):
        response = await async_client.post(
            "/v1/invoke/paid-invoke-service",
            headers={
                **_auth_headers(
                    consumer_account_id,
                    payment_header=_payment_header(payment_identifier="payment-payout-success"),
                ),
                "X-Request-ID": "payout-success-req",
            },
            json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
        )

    payouts = await _list_payouts(db_session_factory)
    ready_record = next(
        record
        for record in caplog.records
        if record.name == "app.services.payout_service"
        and getattr(record, EVENT_FIELD, None) == "payout.ready"
    )

    assert response.status_code == 200
    assert payout_executor.prepare_calls == []
    assert payout_executor.send_calls == []
    assert len(payouts) == 1
    assert payouts[0].status.value == "ready"
    assert payouts[0].destination_wallet is None
    assert payouts[0].amount_minor == 4_500_000
    assert payouts[0].currency == "USDC"
    assert payouts[0].network == "base-sepolia"
    assert payouts[0].attempt_count == 0
    assert getattr(ready_record, REQUEST_ID_FIELD) == "payout-success-req"
    assert getattr(ready_record, PAYOUT_ID_FIELD) == payouts[0].id
    assert getattr(ready_record, PAYOUT_STATUS_FIELD) == "ready"


@pytest.mark.asyncio
async def test_paid_invoke_records_asset_denominated_provider_payout_amount(
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
    payout_executor = _SuccessfulPayoutExecutor()
    _install_payment_state(
        app,
        upstream_client=_FakeHttpClient(
            responses=[Response(status_code=200, json={"result": "bonjour"})]
        ),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[{"ok": True, "reference": "verify-1"}],
            settle_outcomes=[{"ok": True, "reference": "settle-1"}],
        ),
        payout_executor=payout_executor,
        payouts_enabled=True,
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers={
            **_auth_headers(
                consumer_account_id,
                payment_header=_payment_header(payment_identifier="payment-payout-failure"),
            ),
            "X-Request-ID": "payout-failure-req",
        },
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    attempt = await _get_payment_attempt(
        db_session_factory,
        payment_identifier="payment-payout-failure",
    )
    payouts = await _list_payouts(db_session_factory)

    assert response.status_code == 200
    assert attempt is not None
    assert len(payouts) == 1
    assert payout_executor.prepare_calls == []
    assert payout_executor.send_calls == []
    assert payouts[0].status.value == "ready"
    assert payouts[0].currency == "USDC"
    assert payouts[0].amount_minor == 4_500_000
    assert attempt.payment_requirement["payment_amount"] == 5_000_000
    assert payouts[0].transfer_reference is None
    assert payouts[0].destination_wallet is None


@pytest.mark.asyncio
async def test_paid_invoke_replay_does_not_create_duplicate_provider_payout(
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
    payout_executor = _SuccessfulPayoutExecutor()
    _install_payment_state(
        app,
        upstream_client=_FakeHttpClient(
            responses=[Response(status_code=200, json={"result": "bonjour"})]
        ),
        facilitator_client=_FakeFacilitatorClient(
            verify_outcomes=[{"ok": True, "reference": "verify-1"}],
            settle_outcomes=[{"ok": True, "reference": "settle-1"}],
        ),
        payout_executor=payout_executor,
        payouts_enabled=True,
    )
    headers = _auth_headers(
        consumer_account_id,
        payment_header=_payment_header(payment_identifier="payment-payout-replay"),
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

    payouts = await _list_payouts(db_session_factory)

    assert first.status_code == 200
    assert second.status_code == 200
    assert payout_executor.prepare_calls == []
    assert payout_executor.send_calls == []
    assert len(payouts) == 1


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
