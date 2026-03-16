from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import case, desc, func, select

from app.core.enums import PayoutStatus
from app.db.models import Payout

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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


class PayoutRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        destination_wallet: str,
        amount_minor: int,
        currency: str,
        network: str,
        status: PayoutStatus,
        transfer_reference: str | None = None,
        error_message: str | None = None,
        attempt_count: int = 1,
    ) -> Payout:
        payout = Payout(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet=destination_wallet,
            amount_minor=amount_minor,
            currency=currency,
            network=network,
            status=status,
            transfer_reference=transfer_reference,
            error_message=error_message,
            attempt_count=attempt_count,
        )
        self._session.add(payout)
        return payout

    async def list_for_provider(
        self,
        *,
        provider_account_id: int,
        status: PayoutStatus | None = None,
    ) -> list[Payout]:
        statement = select(Payout).where(Payout.provider_account_id == provider_account_id)
        if status is not None:
            statement = statement.where(Payout.status == status)
        statement = statement.order_by(desc(Payout.created_at), desc(Payout.id))
        result = await self._session.scalars(statement)
        return list(result.all())

    async def summarize_for_provider(self, *, provider_account_id: int) -> PayoutSummary | None:
        statement = (
            select(
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
            )
            .where(Payout.provider_account_id == provider_account_id)
            .group_by(Payout.currency)
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return PayoutSummary(
            currency=row.currency,
            total_count=int(row.total_count),
            ready_count=int(row.ready_count),
            pending_count=int(row.pending_count),
            sent_count=int(row.sent_count),
            failed_count=int(row.failed_count),
            total_amount_minor=int(row.total_amount_minor),
            sent_amount_minor=int(row.sent_amount_minor),
        )
