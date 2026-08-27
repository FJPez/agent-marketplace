"""Provider-facing ledger, earnings, and payout reporting reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import load_only

from app.core.enums import LedgerEntryType, PayoutStatus
from app.core.logging import (
    PAYOUT_COUNT_FIELD,
    PAYOUT_STATUS_FIELD,
    PROVIDER_ACCOUNT_ID_FIELD,
    build_event_context,
    get_logger,
)
from app.db.models import LedgerEntry, Payout

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    currency: str
    charge_minor: int
    platform_fee_minor: int
    provider_earning_minor: int
    entry_count: int


@dataclass(frozen=True, slots=True)
class PayoutSummary:
    currency: str | None
    total_count: int
    ready_count: int
    pending_count: int
    sent_count: int
    failed_count: int
    total_amount_minor: int
    sent_amount_minor: int


async def get_provider_ledger(
    *,
    session: AsyncSession,
    account_id: int,
) -> list[LedgerEntry]:
    statement = (
        select(LedgerEntry)
        .where(LedgerEntry.provider_account_id == account_id)
        .order_by(desc(LedgerEntry.created_at), desc(LedgerEntry.id))
    )
    result = await session.scalars(statement)
    return list(result.all())


async def get_provider_earnings(
    *,
    session: AsyncSession,
    account_id: int,
) -> list[LedgerSummary]:
    statement = (
        select(
            LedgerEntry.currency,
            func.coalesce(
                func.sum(
                    case(
                        (
                            LedgerEntry.entry_type == LedgerEntryType.CHARGE,
                            LedgerEntry.amount_minor,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("charge_minor"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            LedgerEntry.entry_type == LedgerEntryType.PLATFORM_FEE,
                            LedgerEntry.amount_minor,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("platform_fee_minor"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            LedgerEntry.entry_type == LedgerEntryType.PROVIDER_EARNING,
                            LedgerEntry.amount_minor,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("provider_earning_minor"),
            func.count(LedgerEntry.id).label("entry_count"),
        )
        .where(LedgerEntry.provider_account_id == account_id)
        .group_by(LedgerEntry.currency)
        .order_by(LedgerEntry.currency)
    )
    rows = await session.execute(statement)
    return [
        LedgerSummary(
            currency=row.currency,
            charge_minor=int(row.charge_minor),
            platform_fee_minor=int(row.platform_fee_minor),
            provider_earning_minor=int(row.provider_earning_minor),
            entry_count=int(row.entry_count),
        )
        for row in rows
    ]


async def get_provider_payouts(
    *,
    session: AsyncSession,
    account_id: int,
    status: PayoutStatus | None = None,
) -> list[Payout]:
    statement = select(Payout).where(Payout.provider_account_id == account_id)
    if status is not None:
        statement = statement.where(Payout.status == status)
    statement = statement.order_by(desc(Payout.created_at), desc(Payout.id)).options(
        # Fetch exactly the response field set; raiseload makes any later access to an
        # excluded column fail loudly instead of lazy-loading it after the read returns.
        load_only(
            Payout.id,
            Payout.service_id,
            Payout.invocation_id,
            Payout.payment_attempt_id,
            Payout.destination_wallet,
            Payout.amount_minor,
            Payout.currency,
            Payout.network,
            Payout.status,
            Payout.failure_code,
            Payout.attempt_count,
            Payout.created_at,
            Payout.updated_at,
            raiseload=True,
        )
    )
    result = await session.scalars(statement)
    payouts = list(result.all())
    logger.info(
        "provider payouts listed",
        extra=build_event_context(
            "payout.reporting_listed",
            **{
                PROVIDER_ACCOUNT_ID_FIELD: account_id,
                PAYOUT_STATUS_FIELD: None if status is None else status.value,
                PAYOUT_COUNT_FIELD: len(payouts),
            },
        ),
    )
    return payouts


async def get_provider_payout_summaries(
    *,
    session: AsyncSession,
    account_id: int,
    status: PayoutStatus | None = None,
) -> list[PayoutSummary]:
    statement = select(
        Payout.currency,
        func.count(Payout.id).label("total_count"),
        func.coalesce(
            func.sum(case((Payout.status == PayoutStatus.READY, 1), else_=0)),
            0,
        ).label("ready_count"),
        func.coalesce(
            func.sum(case((Payout.status == PayoutStatus.PENDING, 1), else_=0)),
            0,
        ).label("pending_count"),
        func.coalesce(
            func.sum(case((Payout.status == PayoutStatus.SENT, 1), else_=0)),
            0,
        ).label("sent_count"),
        func.coalesce(
            func.sum(case((Payout.status == PayoutStatus.FAILED, 1), else_=0)),
            0,
        ).label("failed_count"),
        func.coalesce(func.sum(Payout.amount_minor), 0).label("total_amount_minor"),
        func.coalesce(
            func.sum(
                case(
                    (Payout.status == PayoutStatus.SENT, Payout.amount_minor),
                    else_=0,
                )
            ),
            0,
        ).label("sent_amount_minor"),
    ).where(Payout.provider_account_id == account_id)
    if status is not None:
        statement = statement.where(Payout.status == status)
    statement = statement.group_by(Payout.currency).order_by(Payout.currency)
    rows = (await session.execute(statement)).all()
    return [
        PayoutSummary(
            currency=row.currency,
            total_count=int(row.total_count),
            ready_count=int(row.ready_count),
            pending_count=int(row.pending_count),
            sent_count=int(row.sent_count),
            failed_count=int(row.failed_count),
            total_amount_minor=int(row.total_amount_minor),
            sent_amount_minor=int(row.sent_amount_minor),
        )
        for row in rows
    ]
