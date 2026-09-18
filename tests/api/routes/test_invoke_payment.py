from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select
from tests.fixtures.domain import (
    create_consumer_account_record,
    create_endpoint_price_record,
    create_endpoint_record,
    create_provider_account_record,
    create_quote_record,
    create_service_record,
    create_upstream_record,
)
from tests.fixtures.payment import (
    FakeX402ResourceServer,
    ScriptedFacilitatorClient,
    ScriptedHttpClient,
)
from tests.helpers.auth import auth_headers_for_account_id
from tests.helpers.x402 import (
    build_payment_requirement,
    build_settle_outcome,
    payment_signature_header,
)

from app.core.enums import (
    AccessMode,
    InvocationStatus,
    PaymentAttemptStatus,
    PricingModelType,
    ServiceLifecycle,
)
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
from app.db.models import Invocation, LedgerEntry, PaymentAttempt, Payout
from app.integrations.payouts import PreparedPayout, SentPayout
from app.integrations.x402.facilitator_client import FacilitatorTransportError
from app.integrations.x402.models import PaymentPayload, SettleOutcome, VerifyOutcome
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
    return await create_provider_account_record(
        db_session_factory,
        display_name="Provider",
        wallet_address=wallet_address,
    )


async def _create_consumer_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    return await create_consumer_account_record(
        db_session_factory,
        display_name="Consumer",
    )


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str = "paid-invoke-service",
) -> int:
    return await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        name="Paid Invoke Service",
        summary="Invoke summary",
        description=None,
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
    )


async def _seed_paid_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    currency: str = "USD",
) -> int:
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        name="Translate",
        summary="Translate text",
        description=None,
        access_mode=AccessMode.PAID,
    )
    await create_upstream_record(
        db_session_factory,
        endpoint_id=endpoint_id,
    )
    await create_endpoint_price_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency=currency,
    )
    return endpoint_id


async def _seed_quote(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    endpoint_id: int,
    payload: dict[str, object],
    amount_minor: int = 500,
    currency: str = "USD",
) -> int:
    return await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload=payload,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=amount_minor,
        currency=currency,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


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
    status: PaymentAttemptStatus = PaymentAttemptStatus.CHALLENGED,
    verify_outcome: VerifyOutcome | None,
    settle_outcome: SettleOutcome | None,
) -> int:
    async with db_session_factory.begin() as session:
        attempt = PaymentAttempt(
            consumer_account_id=consumer_account_id,
            quote_id=quote_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            payment_identifier=payment_identifier,
            status=status,
            payment_requirement=build_payment_requirement().model_dump(mode="json"),
            payment_payload=PaymentPayload.from_header(
                payment_signature_header(payment_identifier=payment_identifier)
            ).wire,
            verify_outcome=(
                None if verify_outcome is None else verify_outcome.model_dump(mode="json")
            ),
            settle_outcome=(
                None if settle_outcome is None else settle_outcome.model_dump(mode="json")
            ),
            facilitator_reference="0xsettled",
        )
        session.add(attempt)
        await session.flush()
        return attempt.id


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
        raise RuntimeError("rpc unavailable")

    async def send_prepared_payout(
        self,
        *,
        raw_transaction: str,
        reference: str,
    ) -> SentPayout:
        raise RuntimeError("rpc unavailable")


def _verify_accepted() -> VerifyOutcome:
    return VerifyOutcome(accepted=True, payer="0xpayer", checked_by="facilitator")


def _verify_rejected() -> VerifyOutcome:
    return VerifyOutcome(
        accepted=False,
        reason="invalid_signature",
        checked_by="facilitator",
    )


def _settle_rejected() -> SettleOutcome:
    return SettleOutcome(success=False, error_reason="insufficient_funds")


def _install_payment_state(
    app: FastAPI,
    *,
    upstream_client: ScriptedHttpClient,
    facilitator_client: object,
    x402_resource_server: object | None = None,
    payout_executor: object | None = None,
    payouts_enabled: bool = False,
) -> None:
    state = get_app_state(app)
    state.http_client = upstream_client
    state.facilitator_client = facilitator_client
    state.x402_resource_server = x402_resource_server or FakeX402ResourceServer()
    state.settings.payouts_enabled = payouts_enabled
    state.settings.treasury_private_key = SecretStr("0x" + "11" * 32)
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
        upstream_client=ScriptedHttpClient(),
        facilitator_client=ScriptedFacilitatorClient(verify_results=[], settle_results=[]),
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
    upstream_client = ScriptedHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})]
    )
    facilitator_client = ScriptedFacilitatorClient(
        verify_results=[_verify_accepted()],
        settle_results=[build_settle_outcome()],
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
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )
    payment_attempt = await _get_payment_attempt(
        db_session_factory,
        payment_identifier="payment-1",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["response_payload"] == {"result": "bonjour"}
    assert "PAYMENT-RESPONSE" in response.headers
    assert payment_attempt is not None
    assert payment_attempt.status is PaymentAttemptStatus.CONSUMED
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
    upstream_client = ScriptedHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})]
    )
    facilitator_client = ScriptedFacilitatorClient(
        verify_results=[_verify_accepted()],
        settle_results=[build_settle_outcome()],
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
            payment_header=payment_signature_header(
                payment_identifier="payment-ledger",
            ),
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
        upstream_client=ScriptedHttpClient(
            responses=[Response(status_code=200, json={"result": "bonjour"})]
        ),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[_verify_accepted()],
            settle_results=[build_settle_outcome()],
        ),
    )

    with caplog.at_level(logging.INFO):
        response = await async_client.post(
            "/v1/invoke/paid-invoke-service",
            headers={
                **_auth_headers(
                    consumer_account_id,
                    payment_header=payment_signature_header(
                        payment_identifier="payment-log-success",
                    ),
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
        if record.name == "app.services.invoke"
        and getattr(record, EVENT_FIELD, None) == "invoke.succeeded"
    )
    ledger_record = next(
        record
        for record in caplog.records
        if record.name == "app.services.payment"
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
    upstream_client = ScriptedHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})]
    )
    facilitator_client = ScriptedFacilitatorClient(
        verify_results=[_verify_accepted()],
        settle_results=[build_settle_outcome()],
    )
    _install_payment_state(
        app,
        upstream_client=upstream_client,
        facilitator_client=facilitator_client,
    )
    headers = _auth_headers(
        consumer_account_id,
        payment_header=payment_signature_header(payment_identifier="payment-1"),
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
    assert "PAYMENT-RESPONSE" in second.headers
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
        upstream_client=ScriptedHttpClient(
            responses=[Response(status_code=500, json={"detail": "upstream failed"})]
        ),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[_verify_accepted()],
            settle_results=[build_settle_outcome()],
        ),
    )

    with caplog.at_level(logging.ERROR):
        response = await async_client.post(
            "/v1/invoke/paid-invoke-service",
            headers={
                **_auth_headers(
                    consumer_account_id,
                    payment_header=payment_signature_header(
                        payment_identifier="payment-log-failure",
                    ),
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
    payment_attempt = await _get_payment_attempt(
        db_session_factory,
        payment_identifier="payment-log-failure",
    )
    invocation_count, payment_attempt_count, ledger_count, payout_count = await _count_rows(
        db_session_factory
    )
    assert failed_invocation is not None
    assert payment_attempt is not None
    assert payment_attempt.status is PaymentAttemptStatus.COMPENSATION_REQUIRED
    assert payment_attempt.invocation_id == failed_invocation.id
    assert invocation_count == 1
    assert payment_attempt_count == 1
    assert ledger_count == 0
    assert payout_count == 0
    failure_record = next(
        record
        for record in caplog.records
        if record.name == "app.services.invoke"
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
        upstream_client=ScriptedHttpClient(
            responses=[Response(status_code=200, json={"result": "bonjour"})]
        ),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[_verify_accepted()],
            settle_results=[build_settle_outcome()],
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
                    payment_header=payment_signature_header(
                        payment_identifier="payment-payout-success",
                    ),
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
        upstream_client=ScriptedHttpClient(
            responses=[Response(status_code=200, json={"result": "bonjour"})]
        ),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[_verify_accepted()],
            settle_results=[build_settle_outcome()],
        ),
        payout_executor=payout_executor,
        payouts_enabled=True,
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers={
            **_auth_headers(
                consumer_account_id,
                payment_header=payment_signature_header(
                    payment_identifier="payment-payout-failure",
                ),
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
async def test_paid_invoke_rejects_wrong_payment_token_before_invoke_or_payout(
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
        upstream_client=ScriptedHttpClient(
            responses=[Response(status_code=200, json={"result": "bonjour"})]
        ),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[_verify_accepted()],
            settle_results=[build_settle_outcome()],
        ),
        payouts_enabled=True,
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=payment_signature_header(
                payment_identifier="wrong-token-payment",
                asset="0x00000000000000000000000000000000000000aa",
            ),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    invocation_count, payment_attempt_count, ledger_count, payout_count = await _count_rows(
        db_session_factory
    )
    payment_attempt = await _get_payment_attempt(
        db_session_factory,
        payment_identifier="wrong-token-payment",
    )

    assert response.status_code == 402
    assert response.json() == {"detail": "payment could not be verified"}
    assert invocation_count == 0
    assert payment_attempt_count == 1
    assert ledger_count == 0
    assert payout_count == 0
    assert payment_attempt is not None
    assert payment_attempt.status is PaymentAttemptStatus.VERIFY_FAILED


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
        upstream_client=ScriptedHttpClient(
            responses=[Response(status_code=200, json={"result": "bonjour"})]
        ),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[_verify_accepted()],
            settle_results=[build_settle_outcome()],
        ),
        payout_executor=payout_executor,
        payouts_enabled=True,
    )
    headers = _auth_headers(
        consumer_account_id,
        payment_header=payment_signature_header(
            payment_identifier="payment-payout-replay",
        ),
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
async def test_paid_invoke_rejects_payment_identifier_reuse_for_a_different_idempotency_key(
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
    upstream_client = ScriptedHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})]
    )
    facilitator_client = ScriptedFacilitatorClient(
        verify_results=[_verify_accepted()],
        settle_results=[build_settle_outcome()],
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
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )
    second = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-2",
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert first.status_code == 200
    assert "PAYMENT-RESPONSE" in first.headers
    assert second.status_code == 409
    assert second.json() == {"detail": "payment identifier already used"}
    assert len(upstream_client.calls) == 1


@pytest.mark.asyncio
async def test_paid_invoke_resumes_the_attempt_that_already_owns_the_payment_identifier(
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
    # The challenge was already stored, so the insert this request makes conflicts and
    # the request has to carry the stored attempt forward instead of starting a new one.
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
    upstream_client = ScriptedHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
    )
    facilitator_client = ScriptedFacilitatorClient(
        verify_results=[_verify_accepted()],
        settle_results=[build_settle_outcome()],
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
            idempotency_key="invoke-key-1",
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    _, payment_attempt_count, _, _ = await _count_rows(db_session_factory)
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert payment_attempt_count == 1
    assert len(facilitator_client.verify_calls) == 1
    assert len(facilitator_client.settle_calls) == 1
    assert len(upstream_client.calls) == 1


@pytest.mark.asyncio
async def test_failed_payment_identifier_reuse_replays_verification_challenge(
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
        upstream_client=ScriptedHttpClient(),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[_verify_rejected()],
            settle_results=[],
        ),
    )

    first = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-1",
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )
    second = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-1",
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert first.status_code == 402
    assert second.status_code == 402
    assert second.json() == {"detail": "payment could not be verified"}
    attempt = await _get_payment_attempt(
        db_session_factory,
        payment_identifier="payment-1",
    )
    assert attempt is not None
    assert attempt.status is PaymentAttemptStatus.VERIFY_FAILED


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
        status=PaymentAttemptStatus.CONSUMED,
        verify_outcome=_verify_accepted(),
        settle_outcome=build_settle_outcome(),
    )
    _install_payment_state(
        app,
        upstream_client=ScriptedHttpClient(
            responses=[Response(status_code=200, json={"result": "fresh"})],
        ),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[],
            settle_results=[],
        ),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            idempotency_key="invoke-key-2",
            payment_header=payment_signature_header(payment_identifier="payment-1"),
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
        upstream_client=ScriptedHttpClient(),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[_verify_rejected()],
            settle_results=[],
        ),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )
    attempt = await _get_payment_attempt(
        db_session_factory,
        payment_identifier="payment-1",
    )

    assert response.status_code == 402
    assert response.json() == {"detail": "payment could not be verified"}
    assert "PAYMENT-REQUIRED" in response.headers
    assert attempt is not None
    assert attempt.status is PaymentAttemptStatus.VERIFY_FAILED


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
        upstream_client=ScriptedHttpClient(),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[FacilitatorTransportError("facilitator unavailable")],
        ),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=payment_signature_header(payment_identifier="payment-1"),
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
        upstream_client=ScriptedHttpClient(),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[_verify_accepted()],
            settle_results=[_settle_rejected()],
        ),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )
    attempt = await _get_payment_attempt(
        db_session_factory,
        payment_identifier="payment-1",
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "payment settlement failed"}
    assert attempt is not None
    assert attempt.status is PaymentAttemptStatus.SETTLE_FAILED


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
        upstream_client=ScriptedHttpClient(),
        facilitator_client=ScriptedFacilitatorClient(
            verify_results=[],
            settle_results=[],
        ),
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "paid invoke requires quote"}


@pytest.mark.asyncio
async def test_paid_invoke_refuses_an_attempt_that_still_requires_compensation(
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
    await _seed_payment_attempt(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        quote_id=quote_id,
        invocation_id=None,
        idempotency_key="invoke-key",
        payment_identifier="payment-1",
        status=PaymentAttemptStatus.COMPENSATION_REQUIRED,
        verify_outcome=_verify_accepted(),
        settle_outcome=build_settle_outcome(),
    )
    upstream_client = ScriptedHttpClient()
    facilitator_client = ScriptedFacilitatorClient()
    _install_payment_state(
        app,
        upstream_client=upstream_client,
        facilitator_client=facilitator_client,
    )

    response = await async_client.post(
        "/v1/invoke/paid-invoke-service",
        headers=_auth_headers(
            consumer_account_id,
            payment_header=payment_signature_header(payment_identifier="payment-1"),
        ),
        json={"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": quote_id},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "payment requires compensation; the invocation did not succeed",
    }
    assert upstream_client.calls == []
    assert facilitator_client.settle_calls == []
