from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import (
    AccessMode,
    InvocationStatus,
    LedgerEntryType,
    PricingModelType,
    ServiceLifecycle,
)
from app.db.models import (
    Account,
    ConsumerProfile,
    Invocation,
    PaymentAttempt,
    ProviderProfile,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)
from app.repositories.ledger_entry_repo import LedgerEntryRepository


def _auth_headers(account_id: int) -> dict[str, str]:
    return {"X-Account-Id": str(account_id)}


async def _seed_provider_finance_data(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    async with db_session_factory.begin() as session:
        provider_account = Account()
        consumer_account = Account()
        session.add_all([provider_account, consumer_account])
        await session.flush()
        session.add(ProviderProfile(account_id=provider_account.id, display_name="Provider"))
        session.add(ConsumerProfile(account_id=consumer_account.id, display_name="Consumer"))

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


@pytest.mark.asyncio
async def test_provider_finance_routes_require_x_account_id_header(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/v1/provider/earnings")

    assert response.status_code == 401
    assert response.json() == {"detail": "X-Account-Id header is required"}


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
