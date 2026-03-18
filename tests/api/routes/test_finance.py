import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_consumer_account_record,
    create_endpoint_record,
    create_provider_account_record,
    create_quote_record,
    create_service_record,
)
from tests.helpers.auth import auth_headers_for_account_id

from app.core.enums import (
    AccessMode,
    InvocationStatus,
    LedgerEntryType,
    PayoutStatus,
    PricingModelType,
    ServiceLifecycle,
)
from app.core.lifespan import get_app_state
from app.core.logging import EVENT_FIELD, PAYOUT_COUNT_FIELD, REQUEST_ID_FIELD
from app.core.security import hash_api_key
from app.db.models import (
    Account,
    ApiKey,
    Invocation,
    PaymentAttempt,
    Payout,
    Quote,
    ServiceEndpoint,
)
from app.integrations.payouts import PreparedPayout, SentPayout
from app.repositories.ledger_entry_repo import LedgerEntryRepository
from app.services.ledger_service import LedgerService


def _auth_headers(account_id: int) -> dict[str, str]:
    return auth_headers_for_account_id(account_id)


def _api_key_headers() -> dict[str, str]:
    return {"Authorization": "Bearer amp_test-key"}


async def _seed_provider_finance_data(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    provider_account_id = await create_provider_account_record(
        db_session_factory,
        display_name="Provider",
        wallet_address="0x00000000000000000000000000000000000000aa",
    )
    consumer_account_id = await create_consumer_account_record(
        db_session_factory,
        display_name="Consumer",
    )
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="finance-service",
        name="Finance Service",
        summary="Summary",
        description=None,
        lifecycle=ServiceLifecycle.ACTIVE,
        with_revision=True,
        change_token="e" * 64,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        name="Translate",
        summary=None,
        description=None,
        access_mode=AccessMode.PAID,
    )
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        request_hash="c" * 64,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async with db_session_factory.begin() as session:
        provider_account = await session.get(Account, provider_account_id)
        assert provider_account is not None

        invocation = Invocation(
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key="translate",
            access_mode=AccessMode.PAID,
            quote_id=quote_id,
            idempotency_key="finance-key",
            request_hash="c" * 64,
            status=InvocationStatus.SUCCEEDED,
            response_payload={"result": "ciao"},
            upstream_status_code=200,
            error_message=None,
            failure_reason=None,
        )
        session.add(invocation)
        await session.flush()

        attempt = PaymentAttempt(
            consumer_account_id=consumer_account_id,
            quote_id=quote_id,
            invocation_id=invocation.id,
            idempotency_key="finance-key",
            payment_identifier="payment-finance",
            payment_requirement={"amount_minor": 500, "payment_amount": 5_000_000},
            payment_payload={"payment_identifier": "payment-finance"},
            verify_outcome={"ok": True},
            settle_outcome={"ok": True},
            facilitator_reference="settle-finance",
        )
        session.add(attempt)
        await session.flush()

        repo = LedgerEntryRepository(session)
        repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation.id,
            payment_attempt_id=attempt.id,
            entry_type=LedgerEntryType.CHARGE,
            amount_minor=500,
            currency="USD",
        )
        repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation.id,
            payment_attempt_id=attempt.id,
            entry_type=LedgerEntryType.PLATFORM_FEE,
            amount_minor=50,
            currency="USD",
        )
        repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation.id,
            payment_attempt_id=attempt.id,
            entry_type=LedgerEntryType.PROVIDER_EARNING,
            amount_minor=450,
            currency="USD",
        )
        return provider_account_id, service_id


async def _seed_provider_api_key(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            ApiKey(
                account_id=account_id,
                name="provider-key",
                key_prefix="amp_",
                key_hash=hash_api_key("amp_test-key"),
            )
        )


async def _seed_provider_payout_data(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    provider_account_id, service_id = await _seed_provider_finance_data(db_session_factory)

    async with db_session_factory.begin() as session:
        provider_account = await session.get(Account, provider_account_id)
        assert provider_account is not None

        invocation = await session.scalar(
            select(Invocation).where(Invocation.service_id == service_id).order_by(Invocation.id)
        )
        assert invocation is not None
        retry_invocation = Invocation(
            consumer_account_id=invocation.consumer_account_id,
            service_id=invocation.service_id,
            endpoint_id=invocation.endpoint_id,
            endpoint_key=invocation.endpoint_key,
            access_mode=invocation.access_mode,
            quote_id=invocation.quote_id,
            idempotency_key="finance-key-retry",
            request_hash="d" * 64,
            status=InvocationStatus.SUCCEEDED,
            response_payload={"result": "retry"},
            upstream_status_code=200,
            error_message=None,
            failure_reason=None,
        )
        session.add(retry_invocation)
        await session.flush()
        payment_attempt = await session.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.invocation_id == invocation.id)
            .order_by(PaymentAttempt.id)
        )
        assert payment_attempt is not None
        retry_attempt = PaymentAttempt(
            consumer_account_id=payment_attempt.consumer_account_id,
            quote_id=payment_attempt.quote_id,
            invocation_id=retry_invocation.id,
            idempotency_key="finance-key-retry",
            payment_identifier="payment-finance-retry",
            payment_requirement={"amount_minor": 500, "payment_amount": 5_000_000},
            payment_payload={"payment_identifier": "payment-finance-retry"},
            verify_outcome={"ok": True},
            settle_outcome={"ok": True},
            facilitator_reference="settle-finance-retry",
        )
        session.add(retry_attempt)
        await session.flush()

        session.add_all(
            [
                Payout(
                    provider_account_id=provider_account_id,
                    service_id=service_id,
                    invocation_id=invocation.id,
                    payment_attempt_id=payment_attempt.id,
                    destination_wallet=provider_account.wallet_address,
                    amount_minor=4_500_000,
                    currency="USDC",
                    network="base-sepolia",
                    status=PayoutStatus.SENT,
                    transfer_reference="0xsent",
                    request_idempotency_key="request-sent",
                    failure_code=None,
                    error_message=None,
                    attempt_count=1,
                    prepared_raw_transaction=None,
                    chain_nonce=8,
                ),
                Payout(
                    provider_account_id=provider_account_id,
                    service_id=service_id,
                    invocation_id=retry_invocation.id,
                    payment_attempt_id=retry_attempt.id,
                    destination_wallet=provider_account.wallet_address,
                    amount_minor=4_400_000,
                    currency="USDC",
                    network="base-sepolia",
                    status=PayoutStatus.FAILED,
                    transfer_reference=None,
                    request_idempotency_key="request-failed",
                    failure_code="executor_error",
                    error_message="rpc unavailable",
                    attempt_count=2,
                    prepared_raw_transaction="0xrawtx",
                    chain_nonce=9,
                ),
            ]
        )

    return provider_account_id, service_id


async def _seed_provider_ready_payout_data(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    wallet_address: str = "0x00000000000000000000000000000000000000aa",
) -> tuple[int, int]:
    provider_account_id, service_id = await _seed_provider_finance_data(db_session_factory)

    async with db_session_factory.begin() as session:
        provider_account = await session.get(Account, provider_account_id)
        assert provider_account is not None
        provider_account.wallet_address = wallet_address
        invocation = await session.scalar(
            select(Invocation).where(Invocation.service_id == service_id).order_by(Invocation.id)
        )
        assert invocation is not None
        consumer_account = await session.get(Account, invocation.consumer_account_id)
        quote = await session.scalar(
            select(Quote).where(Quote.service_id == service_id).order_by(Quote.id)
        )
        payment_attempt = await session.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.invocation_id == invocation.id)
            .order_by(PaymentAttempt.id)
        )
        endpoint = await session.scalar(
            select(ServiceEndpoint)
            .where(ServiceEndpoint.service_id == service_id)
            .order_by(ServiceEndpoint.id)
        )
        assert consumer_account is not None
        assert quote is not None
        assert payment_attempt is not None
        assert endpoint is not None

        ready_invocation = Invocation(
            consumer_account_id=consumer_account.id,
            service_id=service_id,
            endpoint_id=endpoint.id,
            endpoint_key="translate",
            access_mode=AccessMode.PAID,
            quote_id=quote.id,
            idempotency_key="finance-ready-key",
            request_hash="f" * 64,
            status=InvocationStatus.SUCCEEDED,
            response_payload={"result": "ready"},
            upstream_status_code=200,
            error_message=None,
            failure_reason=None,
        )
        session.add(ready_invocation)
        await session.flush()

        ready_attempt = PaymentAttempt(
            consumer_account_id=consumer_account.id,
            quote_id=quote.id,
            invocation_id=ready_invocation.id,
            idempotency_key="finance-ready-key",
            payment_identifier="payment-finance-ready",
            payment_requirement={"amount_minor": 500, "payment_amount": 5_000_000},
            payment_payload={"payment_identifier": "payment-finance-ready"},
            verify_outcome={"ok": True},
            settle_outcome={"ok": True},
            facilitator_reference="settle-finance-ready",
        )
        session.add(ready_attempt)
        await session.flush()

        session.add_all(
            [
                Payout(
                    provider_account_id=provider_account_id,
                    service_id=service_id,
                    invocation_id=invocation.id,
                    payment_attempt_id=payment_attempt.id,
                    destination_wallet=None,
                    amount_minor=4_500_000,
                    currency="USDC",
                    network="base-sepolia",
                    status=PayoutStatus.READY,
                    transfer_reference=None,
                    request_idempotency_key=None,
                    failure_code=None,
                    error_message=None,
                    attempt_count=0,
                    prepared_raw_transaction=None,
                    chain_nonce=None,
                ),
                Payout(
                    provider_account_id=provider_account_id,
                    service_id=service_id,
                    invocation_id=ready_invocation.id,
                    payment_attempt_id=ready_attempt.id,
                    destination_wallet=None,
                    amount_minor=4_500_000,
                    currency="USDC",
                    network="base-sepolia",
                    status=PayoutStatus.READY,
                    transfer_reference=None,
                    request_idempotency_key=None,
                    failure_code=None,
                    error_message=None,
                    attempt_count=0,
                    prepared_raw_transaction=None,
                    chain_nonce=None,
                ),
            ]
        )

    return provider_account_id, service_id


@dataclass
class _SuccessfulPayoutExecutor:
    prepare_calls: list[dict[str, object]] = field(default_factory=list)
    send_calls: list[dict[str, object]] = field(default_factory=list)

    async def current_nonce(self) -> int:
        return 9

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
            raw_transaction=f"0xrawtx{nonce}",
            reference=f"0xsent{nonce}",
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


def _install_payout_state(app: FastAPI, *, payout_executor: object) -> None:
    state = get_app_state(app)
    state.settings.payouts_enabled = True
    state.payout_executor = payout_executor


@pytest.mark.asyncio
async def test_provider_finance_routes_require_bearer_token(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/v1/provider/earnings")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authorization header is required"}


@pytest.mark.asyncio
async def test_provider_payouts_route_requires_bearer_token(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/v1/provider/payouts")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authorization header is required"}


@pytest.mark.asyncio
async def test_provider_payout_request_route_requires_bearer_token(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post("/v1/provider/payouts")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authorization header is required"}


@pytest.mark.asyncio
async def test_provider_payout_request_requires_idempotency_key(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, _ = await _seed_provider_ready_payout_data(db_session_factory)

    response = await async_client.post(
        "/v1/provider/payouts",
        headers=_auth_headers(provider_account_id),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_provider_payout_request_rejects_api_key_auth(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, _ = await _seed_provider_ready_payout_data(db_session_factory)
    await _seed_provider_api_key(db_session_factory, account_id=provider_account_id)
    _install_payout_state(app, payout_executor=_SuccessfulPayoutExecutor())

    response = await async_client.post(
        "/v1/provider/payouts",
        headers={**_api_key_headers(), "Idempotency-Key": "payout-request-api-key"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "jwt authentication required"}


@pytest.mark.asyncio
async def test_get_provider_earnings_returns_currency_totals(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, _ = await _seed_provider_finance_data(db_session_factory)

    response = await async_client.get(
        "/v1/provider/earnings",
        headers=_auth_headers(provider_account_id),
    )

    assert response.status_code == 200
    assert response.json() == {
        "totals": [
            {
                "currency": "USD",
                "charge_minor": 500,
                "platform_fee_minor": 50,
                "provider_earning_minor": 450,
                "entry_count": 3,
            }
        ]
    }


@pytest.mark.asyncio
async def test_get_provider_ledger_returns_entries_newest_first(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, service_id = await _seed_provider_finance_data(db_session_factory)

    response = await async_client.get(
        "/v1/provider/ledger",
        headers=_auth_headers(provider_account_id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["entry_type"] for item in payload["entries"]] == [
        "provider_earning",
        "platform_fee",
        "charge",
    ]
    assert all(item["service_id"] == service_id for item in payload["entries"])


@pytest.mark.asyncio
async def test_get_provider_payouts_returns_provider_scoped_records_and_summaries(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider_account_id, service_id = await _seed_provider_payout_data(db_session_factory)

    with caplog.at_level(logging.INFO, logger="app.services.payout_service"):
        response = await async_client.get(
            "/v1/provider/payouts",
            headers={**_auth_headers(provider_account_id), "X-Request-ID": "payout-list-req"},
        )

    assert response.status_code == 200
    body = response.json()
    expected_summary = {
        "currency": "USDC",
        "total_count": 2,
        "ready_count": 0,
        "pending_count": 0,
        "sent_count": 1,
        "failed_count": 1,
        "total_amount_minor": 8_900_000,
        "sent_amount_minor": 4_500_000,
    }
    assert body["summaries"] == [expected_summary]
    assert "summary" not in body
    payouts = body["payouts"]
    assert len(payouts) == 2
    assert payouts[0]["service_id"] == service_id
    assert payouts[0]["status"] == "failed"
    assert payouts[0]["amount_minor"] == 4_400_000
    assert payouts[0]["destination_wallet"] == "0x00000000000000000000000000000000000000aa"
    assert payouts[0]["failure_code"] == "executor_error"
    assert "transfer_reference" not in payouts[0]
    assert "error_message" not in payouts[0]
    assert payouts[0]["attempt_count"] == 2
    assert isinstance(payouts[0]["id"], int)
    assert isinstance(payouts[0]["invocation_id"], int)
    assert isinstance(payouts[0]["payment_attempt_id"], int)
    assert payouts[1]["service_id"] == service_id
    assert payouts[1]["status"] == "sent"
    assert payouts[1]["amount_minor"] == 4_500_000
    assert payouts[1]["destination_wallet"] == "0x00000000000000000000000000000000000000aa"
    assert payouts[1]["failure_code"] is None
    assert "transfer_reference" not in payouts[1]
    assert "error_message" not in payouts[1]
    assert payouts[1]["attempt_count"] == 1
    assert isinstance(payouts[1]["id"], int)
    assert isinstance(payouts[1]["invocation_id"], int)
    assert isinstance(payouts[1]["payment_attempt_id"], int)
    record = next(
        record
        for record in caplog.records
        if record.name == "app.services.payout_service"
        and getattr(record, EVENT_FIELD, None) == "payout.reporting_listed"
    )
    assert getattr(record, EVENT_FIELD) == "payout.reporting_listed"
    assert getattr(record, REQUEST_ID_FIELD) == "payout-list-req"
    assert getattr(record, PAYOUT_COUNT_FIELD) == 2


@pytest.mark.asyncio
async def test_get_provider_payouts_filters_by_status(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, _ = await _seed_provider_payout_data(db_session_factory)

    response = await async_client.get(
        "/v1/provider/payouts?status=failed",
        headers=_auth_headers(provider_account_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summaries"] == [
        {
            "currency": "USDC",
            "total_count": 1,
            "ready_count": 0,
            "pending_count": 0,
            "sent_count": 0,
            "failed_count": 1,
            "total_amount_minor": 4_400_000,
            "sent_amount_minor": 0,
        }
    ]
    assert [item["status"] for item in body["payouts"]] == ["failed"]


@pytest.mark.asyncio
async def test_request_provider_payouts_claims_ready_rows_and_replays_by_idempotency_key(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, _ = await _seed_provider_ready_payout_data(db_session_factory)
    payout_executor = _SuccessfulPayoutExecutor()
    _install_payout_state(app, payout_executor=payout_executor)

    first = await async_client.post(
        "/v1/provider/payouts",
        headers={**_auth_headers(provider_account_id), "Idempotency-Key": "payout-request-1"},
    )
    second = await async_client.post(
        "/v1/provider/payouts",
        headers={**_auth_headers(provider_account_id), "Idempotency-Key": "payout-request-1"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["idempotency_key"] == "payout-request-1"
    assert first.json()["requested_count"] == 2
    assert first.json()["sent_count"] == 2
    assert first.json()["failed_count"] == 0
    assert len(first.json()["payouts"]) == 2
    assert {item["status"] for item in first.json()["payouts"]} == {"sent"}
    assert payout_executor.prepare_calls == [
        {
            "destination_wallet": "0x00000000000000000000000000000000000000aa",
            "amount_minor": 4_500_000,
            "idempotency_key": "payout-request-1",
            "nonce": 9,
        },
        {
            "destination_wallet": "0x00000000000000000000000000000000000000aa",
            "amount_minor": 4_500_000,
            "idempotency_key": "payout-request-1",
            "nonce": 10,
        },
    ]
    assert payout_executor.send_calls == [
        {
            "raw_transaction": "0xrawtx9",
            "reference": "0xsent9",
        },
        {
            "raw_transaction": "0xrawtx10",
            "reference": "0xsent10",
        },
    ]


@pytest.mark.asyncio
async def test_request_provider_payouts_returns_conflict_when_no_ready_rows(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, _ = await _seed_provider_payout_data(db_session_factory)
    _install_payout_state(app, payout_executor=_SuccessfulPayoutExecutor())

    response = await async_client.post(
        "/v1/provider/payouts",
        headers={**_auth_headers(provider_account_id), "Idempotency-Key": "payout-request-empty"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "no ready payouts available"}


@pytest.mark.asyncio
async def test_request_provider_payouts_returns_conflict_when_wallet_missing(
    app: FastAPI,
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, _ = await _seed_provider_ready_payout_data(
        db_session_factory,
        wallet_address="",
    )
    _install_payout_state(app, payout_executor=_SuccessfulPayoutExecutor())

    response = await async_client.post(
        "/v1/provider/payouts",
        headers={**_auth_headers(provider_account_id), "Idempotency-Key": "payout-request-wallet"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "provider wallet address is not configured"}


@pytest.mark.asyncio
async def test_finance_routes_do_not_leak_internal_exceptions(
    app: FastAPI,
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = migrated_database
    provider_account_id, _ = await _seed_provider_finance_data(db_session_factory)

    async def explode(self: LedgerService, actor: object) -> list[object]:
        _ = self, actor
        raise RuntimeError("sensitive database details")

    monkeypatch.setattr(LedgerService, "get_provider_earnings", explode)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as async_client:
            response = await async_client.get(
                "/v1/provider/earnings",
                headers=_auth_headers(provider_account_id),
            )

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
