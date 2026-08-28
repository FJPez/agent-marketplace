import pytest
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_consumer_account_record,
    create_endpoint_price_record,
    create_endpoint_record,
    create_invocation_record,
    create_ledger_entry_record,
    create_payment_attempt_record,
    create_payout_record,
    create_provider_account_record,
    create_quote_record,
    create_service_record,
)

from app.core.enums import AccessMode, LedgerEntryType, PaymentAttemptStatus, PayoutStatus
from app.db.models import LedgerEntry
from app.services import finance
from app.services.ledger_service import LedgerService

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]


async def seed_provider_context(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    attempt_count: int = 1,
) -> tuple[int, int, list[tuple[int, int]]]:
    provider_account_id = await create_provider_account_record(
        db_session_factory,
        display_name="Provider",
    )
    consumer_account_id = await create_consumer_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency="USD",
    )
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
    )

    attempts: list[tuple[int, int]] = []
    for index in range(attempt_count):
        idempotency_key = f"{slug}-key-{index}"
        invocation_id = await create_invocation_record(
            db_session_factory,
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            access_mode=AccessMode.PAID,
            quote_id=quote_id,
            payload={"text": idempotency_key},
            idempotency_key=idempotency_key,
        )
        payment_attempt_id = await create_payment_attempt_record(
            db_session_factory,
            consumer_account_id=consumer_account_id,
            quote_id=quote_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            payment_identifier=f"{slug}-payment-{index}",
            status=PaymentAttemptStatus.CONSUMED,
            settle_outcome={"ok": True},
        )
        attempts.append((invocation_id, payment_attempt_id))

    return provider_account_id, service_id, attempts


async def test_get_provider_ledger_returns_provider_entries_newest_first(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, service_id, attempts = await seed_provider_context(
        db_session_factory,
        slug="ledger-read",
    )
    invocation_id, payment_attempt_id = attempts[0]
    for entry_type, amount_minor in (
        (LedgerEntryType.CHARGE, 500),
        (LedgerEntryType.PLATFORM_FEE, 50),
        (LedgerEntryType.PROVIDER_EARNING, 450),
    ):
        await create_ledger_entry_record(
            db_session_factory,
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            entry_type=entry_type,
            amount_minor=amount_minor,
        )

    other_provider_id, other_service_id, other_attempts = await seed_provider_context(
        db_session_factory,
        slug="ledger-read-other",
    )
    other_invocation_id, other_payment_attempt_id = other_attempts[0]
    await create_ledger_entry_record(
        db_session_factory,
        provider_account_id=other_provider_id,
        service_id=other_service_id,
        invocation_id=other_invocation_id,
        payment_attempt_id=other_payment_attempt_id,
        entry_type=LedgerEntryType.CHARGE,
        amount_minor=900,
    )

    async with db_session_factory() as session:
        entries = await finance.get_provider_ledger(
            session=session,
            account_id=provider_account_id,
        )

    assert [entry.entry_type for entry in entries] == [
        LedgerEntryType.PROVIDER_EARNING,
        LedgerEntryType.PLATFORM_FEE,
        LedgerEntryType.CHARGE,
    ]
    assert [entry.amount_minor for entry in entries] == [450, 50, 500]
    assert all(entry.service_id == service_id for entry in entries)


async def test_get_provider_earnings_aggregates_per_currency(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, service_id, attempts = await seed_provider_context(
        db_session_factory,
        slug="earnings-read",
    )
    invocation_id, payment_attempt_id = attempts[0]
    for entry_type, amount_minor, currency in (
        (LedgerEntryType.CHARGE, 500, "USD"),
        (LedgerEntryType.PLATFORM_FEE, 50, "USD"),
        (LedgerEntryType.PROVIDER_EARNING, 450, "USD"),
        (LedgerEntryType.CHARGE, 5_000_000, "USDC"),
        (LedgerEntryType.PROVIDER_EARNING, 4_500_000, "USDC"),
    ):
        await create_ledger_entry_record(
            db_session_factory,
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            entry_type=entry_type,
            amount_minor=amount_minor,
            currency=currency,
        )

    async with db_session_factory() as session:
        totals = await finance.get_provider_earnings(
            session=session,
            account_id=provider_account_id,
        )

    assert totals == [
        finance.LedgerSummary(
            currency="USD",
            charge_minor=500,
            platform_fee_minor=50,
            provider_earning_minor=450,
            entry_count=3,
        ),
        finance.LedgerSummary(
            currency="USDC",
            charge_minor=5_000_000,
            platform_fee_minor=0,
            provider_earning_minor=4_500_000,
            entry_count=2,
        ),
    ]


async def test_get_provider_payouts_orders_filters_and_omits_unloaded_columns(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, service_id, attempts = await seed_provider_context(
        db_session_factory,
        slug="payout-read",
        attempt_count=2,
    )
    for (invocation_id, payment_attempt_id), status, amount_minor in (
        (attempts[0], PayoutStatus.SENT, 4_500_000),
        (attempts[1], PayoutStatus.FAILED, 4_400_000),
    ):
        await create_payout_record(
            db_session_factory,
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet="0x00000000000000000000000000000000000000aa",
            amount_minor=amount_minor,
            status=status,
            prepared_raw_transaction="0xraw",
        )

    async with db_session_factory() as session:
        payouts = await finance.get_provider_payouts(
            session=session,
            account_id=provider_account_id,
        )
        failed = await finance.get_provider_payouts(
            session=session,
            account_id=provider_account_id,
            status=PayoutStatus.FAILED,
        )

        assert [payout.status for payout in payouts] == [PayoutStatus.FAILED, PayoutStatus.SENT]
        assert [payout.amount_minor for payout in payouts] == [4_400_000, 4_500_000]
        assert all(payout.service_id == service_id for payout in payouts)
        assert [payout.id for payout in failed] == [payouts[0].id]

        with pytest.raises(InvalidRequestError):
            _ = payouts[0].prepared_raw_transaction


async def test_get_provider_payout_summaries_counts_statuses_per_currency(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, service_id, attempts = await seed_provider_context(
        db_session_factory,
        slug="payout-summary",
        attempt_count=3,
    )
    for (invocation_id, payment_attempt_id), status, amount_minor, currency in (
        (attempts[0], PayoutStatus.SENT, 4_500_000, "USDC"),
        (attempts[1], PayoutStatus.FAILED, 4_400_000, "USDC"),
        (attempts[2], PayoutStatus.READY, 450, "USD"),
    ):
        await create_payout_record(
            db_session_factory,
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            amount_minor=amount_minor,
            currency=currency,
            status=status,
        )

    async with db_session_factory() as session:
        summaries = await finance.get_provider_payout_summaries(
            session=session,
            account_id=provider_account_id,
        )
        sent_summaries = await finance.get_provider_payout_summaries(
            session=session,
            account_id=provider_account_id,
            status=PayoutStatus.SENT,
        )

    assert summaries == [
        finance.PayoutSummary(
            currency="USD",
            total_count=1,
            ready_count=1,
            pending_count=0,
            sent_count=0,
            failed_count=0,
            total_amount_minor=450,
            sent_amount_minor=0,
        ),
        finance.PayoutSummary(
            currency="USDC",
            total_count=2,
            ready_count=0,
            pending_count=0,
            sent_count=1,
            failed_count=1,
            total_amount_minor=8_900_000,
            sent_amount_minor=4_500_000,
        ),
    ]
    assert sent_summaries == [
        finance.PayoutSummary(
            currency="USDC",
            total_count=1,
            ready_count=0,
            pending_count=0,
            sent_count=1,
            failed_count=0,
            total_amount_minor=4_500_000,
            sent_amount_minor=4_500_000,
        )
    ]


async def test_finance_reads_are_empty_for_a_provider_without_activity(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(
        db_session_factory,
        display_name="Quiet Provider",
    )

    async with db_session_factory() as session:
        assert (
            await finance.get_provider_ledger(session=session, account_id=provider_account_id) == []
        )
        assert (
            await finance.get_provider_earnings(session=session, account_id=provider_account_id)
            == []
        )
        assert (
            await finance.get_provider_payouts(session=session, account_id=provider_account_id)
            == []
        )
        assert (
            await finance.get_provider_payout_summaries(
                session=session,
                account_id=provider_account_id,
            )
            == []
        )


async def test_record_paid_invocation_writes_charge_fee_and_provider_earning_entries(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id, service_id, attempts = await seed_provider_context(
        db_session_factory,
        slug="ledger-write",
    )
    invocation_id, payment_attempt_id = attempts[0]

    async with db_session_factory.begin() as session:
        service = LedgerService(session)
        await service.record_paid_invocation(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            amount_minor=500,
            currency="USD",
        )

    async with db_session_factory() as session:
        result = await session.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.provider_account_id == provider_account_id)
            .order_by(LedgerEntry.id)
        )
        entries = list(result.all())

    assert [entry.entry_type for entry in entries] == [
        LedgerEntryType.CHARGE,
        LedgerEntryType.PLATFORM_FEE,
        LedgerEntryType.PROVIDER_EARNING,
    ]
    assert [entry.amount_minor for entry in entries] == [500, 50, 450]
    assert all(entry.invocation_id == invocation_id for entry in entries)
