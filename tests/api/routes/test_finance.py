import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import auth_headers_for_account_id

from app.core.enums import (
    AccessMode,
    InvocationStatus,
    LedgerEntryType,
    PayoutStatus,
    PricingModelType,
    ServiceLifecycle,
)
from app.core.logging import EVENT_FIELD, PAYOUT_COUNT_FIELD, REQUEST_ID_FIELD
from app.db.models import (
    Account,
    Invocation,
    PaymentAttempt,
    Payout,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)
from app.repositories.ledger_entry_repo import LedgerEntryRepository
from app.services.ledger_service import LedgerService


def _auth_headers(account_id: int) -> dict[str, str]:
    return auth_headers_for_account_id(account_id)


async def _seed_provider_finance_data(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    async with db_session_factory.begin() as session:
        provider_account = Account(
            display_name="Provider",
            wallet_address="0x00000000000000000000000000000000000000aa",
        )
        consumer_account = Account(display_name="Consumer")
        session.add_all([provider_account, consumer_account])
        await session.flush()

        service = Service(
            provider_account_id=provider_account.id,
            slug="finance-service",
            name="Finance Service",
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
            change_token="e" * 64,
            snapshot={"slug": service.slug},
        )
        session.add(revision)
        await session.flush()

        quote = Quote(
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key=endpoint.key,
            request_hash="c" * 64,
            pricing_type=PricingModelType.FIXED_PER_CALL,
            amount_minor=500,
            currency="USD",
            service_revision_id=revision.id,
            service_change_token=revision.change_token,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(quote)
        await session.flush()

        invocation = Invocation(
            consumer_account_id=consumer_account.id,
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key=endpoint.key,
            access_mode=AccessMode.PAID,
            quote_id=quote.id,
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
            consumer_account_id=consumer_account.id,
            quote_id=quote.id,
            invocation_id=invocation.id,
            idempotency_key="finance-key",
            payment_identifier="payment-finance",
            payment_requirement={"amount_minor": 500},
            payment_payload={"payment_identifier": "payment-finance"},
            verify_outcome={"ok": True},
            settle_outcome={"ok": True},
            facilitator_reference="settle-finance",
        )
        session.add(attempt)
        await session.flush()

        repo = LedgerEntryRepository(session)
        repo.add(
            provider_account_id=provider_account.id,
            service_id=service.id,
            invocation_id=invocation.id,
            payment_attempt_id=attempt.id,
            entry_type=LedgerEntryType.CHARGE,
            amount_minor=500,
            currency="USD",
        )
        repo.add(
            provider_account_id=provider_account.id,
            service_id=service.id,
            invocation_id=invocation.id,
            payment_attempt_id=attempt.id,
            entry_type=LedgerEntryType.PLATFORM_FEE,
            amount_minor=50,
            currency="USD",
        )
        repo.add(
            provider_account_id=provider_account.id,
            service_id=service.id,
            invocation_id=invocation.id,
            payment_attempt_id=attempt.id,
            entry_type=LedgerEntryType.PROVIDER_EARNING,
            amount_minor=450,
            currency="USD",
        )
        return provider_account.id, service.id


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
            payment_requirement={"amount_minor": 500},
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
                    error_message=None,
                    attempt_count=1,
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
                    error_message="rpc unavailable",
                    attempt_count=2,
                ),
            ]
        )

    return provider_account_id, service_id


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
async def test_get_provider_payouts_returns_provider_scoped_records_and_summary(
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
    assert body["summary"] == {
        "currency": "USDC",
        "total_count": 2,
        "ready_count": 0,
        "pending_count": 0,
        "sent_count": 1,
        "failed_count": 1,
        "total_amount_minor": 8_900_000,
        "sent_amount_minor": 4_500_000,
    }
    payouts = body["payouts"]
    assert len(payouts) == 2
    assert payouts[0]["service_id"] == service_id
    assert payouts[0]["status"] == "failed"
    assert payouts[0]["amount_minor"] == 4_400_000
    assert payouts[0]["destination_wallet"] == "0x00000000000000000000000000000000000000aa"
    assert payouts[0]["transfer_reference"] is None
    assert payouts[0]["error_message"] == "rpc unavailable"
    assert payouts[0]["attempt_count"] == 2
    assert isinstance(payouts[0]["id"], int)
    assert isinstance(payouts[0]["invocation_id"], int)
    assert isinstance(payouts[0]["payment_attempt_id"], int)
    assert payouts[1]["service_id"] == service_id
    assert payouts[1]["status"] == "sent"
    assert payouts[1]["amount_minor"] == 4_500_000
    assert payouts[1]["destination_wallet"] == "0x00000000000000000000000000000000000000aa"
    assert payouts[1]["transfer_reference"] == "0xsent"
    assert payouts[1]["error_message"] is None
    assert payouts[1]["attempt_count"] == 1
    assert isinstance(payouts[1]["id"], int)
    assert isinstance(payouts[1]["invocation_id"], int)
    assert isinstance(payouts[1]["payment_attempt_id"], int)
    record = next(
        record for record in caplog.records if record.name == "app.services.payout_service"
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
    assert response.json()["summary"]["failed_count"] == 1
    assert [item["status"] for item in response.json()["payouts"]] == ["failed"]


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
