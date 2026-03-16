from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import case, desc, func, select

from app.core.enums import LedgerEntryType
from app.db.models import LedgerEntry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    currency: str
    charge_minor: int
    platform_fee_minor: int
    provider_earning_minor: int
    entry_count: int


class LedgerEntryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        entry_type: LedgerEntryType,
        amount_minor: int,
        currency: str,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            entry_type=entry_type,
            amount_minor=amount_minor,
            currency=currency,
        )
        self._session.add(entry)
        return entry

    async def list_for_provider(self, *, provider_account_id: int) -> list[LedgerEntry]:
        statement = (
            select(LedgerEntry)
            .where(LedgerEntry.provider_account_id == provider_account_id)
            .order_by(desc(LedgerEntry.created_at), desc(LedgerEntry.id))
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def summarize_for_provider(self, *, provider_account_id: int) -> list[LedgerSummary]:
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
            .where(LedgerEntry.provider_account_id == provider_account_id)
            .group_by(LedgerEntry.currency)
            .order_by(LedgerEntry.currency)
        )
        rows = await self._session.execute(statement)
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
