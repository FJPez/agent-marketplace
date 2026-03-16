from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
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
    Invocation,
    PaymentAttempt,
    PricingModel,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)
from app.repositories.ledger_entry_repo import LedgerEntryRepository


@pytest.mark.asyncio
async def test_ledger_entry_repository_persists_lists_and_summarizes_provider_entries(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        provider_account = Account(display_name="Provider")
        consumer_account = Account(display_name="Consumer")
        session.add_all([provider_account, consumer_account])
        await session.flush()

        service = Service(
            provider_account_id=provider_account.id,
            slug="ledger-service",
            name="Ledger Service",
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
            change_token="c" * 64,
            snapshot={"slug": service.slug},
        )
        session.add(revision)
        await session.flush()

        quote = Quote(
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key=endpoint.key,
            request_hash="a" * 64,
            pricing_type=PricingModelType.FIXED_PER_CALL,
            amount_minor=500,
            currency="USD",
            service_revision_id=revision.id,
            service_change_token=revision.change_token,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(quote)
        session.add(
            PricingModel(
                endpoint_id=endpoint.id,
                pricing_type=PricingModelType.FIXED_PER_CALL,
                amount_minor=500,
                currency="USD",
            )
        )
        await session.flush()

        invocation = Invocation(
            consumer_account_id=consumer_account.id,
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key=endpoint.key,
            access_mode=AccessMode.PAID,
            quote_id=quote.id,
            idempotency_key="invoke-key",
            request_hash="a" * 64,
            status=InvocationStatus.SUCCEEDED,
            response_payload={"result": "bonjour"},
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
            idempotency_key="invoke-key",
            payment_identifier="payment-1",
            payment_requirement={"amount_minor": 500},
            payment_payload={"payment_identifier": "payment-1"},
            verify_outcome={"ok": True},
            settle_outcome={"ok": True},
            facilitator_reference="settle-1",
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
        await session.flush()

    async with db_session_factory() as session:
        repo = LedgerEntryRepository(session)
        entries = await repo.list_for_provider(provider_account_id=provider_account.id)
        totals = await repo.summarize_for_provider(provider_account_id=provider_account.id)

    assert [entry.entry_type for entry in entries] == [
        LedgerEntryType.PROVIDER_EARNING,
        LedgerEntryType.PLATFORM_FEE,
        LedgerEntryType.CHARGE,
    ]
    assert entries[0].payment_attempt_id == attempt.id
    assert len(totals) == 1
    assert totals[0].currency == "USD"
    assert totals[0].charge_minor == 500
    assert totals[0].platform_fee_minor == 50
    assert totals[0].provider_earning_minor == 450
    assert totals[0].entry_count == 3


@pytest.mark.asyncio
async def test_ledger_entries_are_immutable_in_database(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        provider_account = Account(display_name="Provider")
        consumer_account = Account(display_name="Consumer")
        session.add_all([provider_account, consumer_account])
        await session.flush()

        service = Service(
            provider_account_id=provider_account.id,
            slug="ledger-immutability",
            name="Ledger Immutability",
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
            change_token="d" * 64,
            snapshot={"slug": service.slug},
        )
        session.add(revision)
        await session.flush()

        quote = Quote(
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key=endpoint.key,
            request_hash="b" * 64,
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
            idempotency_key="immutability-key",
            request_hash="b" * 64,
            status=InvocationStatus.SUCCEEDED,
            response_payload={"result": "hola"},
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
            idempotency_key="immutability-key",
            payment_identifier="payment-immutability",
            payment_requirement={"amount_minor": 500},
            payment_payload={"payment_identifier": "payment-immutability"},
            verify_outcome={"ok": True},
            settle_outcome={"ok": True},
            facilitator_reference="settle-immutability",
        )
        session.add(attempt)
        await session.flush()

        repo = LedgerEntryRepository(session)
        entry = repo.add(
            provider_account_id=provider_account.id,
            service_id=service.id,
            invocation_id=invocation.id,
            payment_attempt_id=attempt.id,
            entry_type=LedgerEntryType.PROVIDER_EARNING,
            amount_minor=450,
            currency="USD",
        )
        await session.flush()
        entry_id = entry.id

    async with db_session_factory.begin() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(
                text(
                    "UPDATE ledger_entries SET amount_minor = 999 WHERE id = :entry_id",
                ),
                {"entry_id": entry_id},
            )

    async with db_session_factory.begin() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(
                text("DELETE FROM ledger_entries WHERE id = :entry_id"),
                {"entry_id": entry_id},
            )
