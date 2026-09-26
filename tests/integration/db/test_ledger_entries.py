"""What the database itself guarantees about ledger entries."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_consumer_account_record,
    create_endpoint_price_record,
    create_endpoint_record,
    create_invocation_record,
    create_ledger_entry_record,
    create_payment_attempt_record,
    create_provider_account_record,
    create_quote_record,
    create_service_record,
)

from app.core.enums import AccessMode, LedgerEntryType, PaymentAttemptStatus, PricingModelType

pytestmark = [pytest.mark.asyncio]


@pytest.fixture
async def recorded_ledger_entry(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    """One provider earning entry, with the ids of the entry and of its payment attempt."""
    provider_account_id = await create_provider_account_record(db_session_factory)
    consumer_account_id = await create_consumer_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="ledger-entries",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
    )
    invocation_id = await create_invocation_record(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        access_mode=AccessMode.PAID,
        quote_id=quote_id,
    )
    payment_attempt_id = await create_payment_attempt_record(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        quote_id=quote_id,
        invocation_id=invocation_id,
        status=PaymentAttemptStatus.CONSUMED,
    )
    entry_id = await create_ledger_entry_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        service_id=service_id,
        invocation_id=invocation_id,
        payment_attempt_id=payment_attempt_id,
        entry_type=LedgerEntryType.PROVIDER_EARNING,
        amount_minor=450,
    )
    return entry_id, payment_attempt_id


async def test_a_recorded_ledger_entry_cannot_be_updated(
    db_session_factory: async_sessionmaker[AsyncSession],
    recorded_ledger_entry: tuple[int, int],
) -> None:
    entry_id, _ = recorded_ledger_entry

    async with db_session_factory.begin() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(
                text("UPDATE ledger_entries SET amount_minor = 999 WHERE id = :entry_id"),
                {"entry_id": entry_id},
            )


async def test_a_recorded_ledger_entry_cannot_be_deleted(
    db_session_factory: async_sessionmaker[AsyncSession],
    recorded_ledger_entry: tuple[int, int],
) -> None:
    entry_id, _ = recorded_ledger_entry

    async with db_session_factory.begin() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(
                text("DELETE FROM ledger_entries WHERE id = :entry_id"),
                {"entry_id": entry_id},
            )


async def test_one_payment_attempt_can_record_each_entry_type_only_once(
    db_session_factory: async_sessionmaker[AsyncSession],
    recorded_ledger_entry: tuple[int, int],
) -> None:
    entry_id, payment_attempt_id = recorded_ledger_entry

    async with db_session_factory() as session:
        source = await session.execute(
            text(
                """
                SELECT provider_account_id, service_id, invocation_id
                FROM ledger_entries
                WHERE id = :entry_id
                """,
            ),
            {"entry_id": entry_id},
        )
        provider_account_id, service_id, invocation_id = source.one()

    with pytest.raises(IntegrityError):
        await create_ledger_entry_record(
            db_session_factory,
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            entry_type=LedgerEntryType.PROVIDER_EARNING,
            amount_minor=450,
        )
