from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select, text

from app.core.enums import PayoutFailureCode, PayoutStatus
from app.db.models import Payout

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_PAYOUT_TREASURY_LOCK_KEY = 84_532_001


class PayoutExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        destination_wallet: str | None,
        amount_minor: int,
        currency: str,
        network: str,
        status: PayoutStatus,
        transfer_reference: str | None = None,
        request_idempotency_key: str | None = None,
        failure_code: PayoutFailureCode | None = None,
        error_message: str | None = None,
        attempt_count: int = 1,
        prepared_raw_transaction: str | None = None,
        chain_nonce: int | None = None,
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
            request_idempotency_key=request_idempotency_key,
            failure_code=failure_code,
            error_message=error_message,
            attempt_count=attempt_count,
            prepared_raw_transaction=prepared_raw_transaction,
            chain_nonce=chain_nonce,
        )
        self._session.add(payout)
        return payout

    async def get_by_payment_attempt_id(self, *, payment_attempt_id: int) -> Payout | None:
        statement = select(Payout).where(Payout.payment_attempt_id == payment_attempt_id)
        return await self._session.scalar(statement)

    async def claim_treasury_lock(self) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _PAYOUT_TREASURY_LOCK_KEY},
        )

    async def list_for_provider_request(
        self,
        *,
        provider_account_id: int,
        request_idempotency_key: str,
        for_update: bool = False,
    ) -> list[Payout]:
        statement = (
            select(Payout)
            .where(
                Payout.provider_account_id == provider_account_id,
                Payout.request_idempotency_key == request_idempotency_key,
            )
            .order_by(Payout.created_at, Payout.id)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return list(result.all())

    async def list_in_flight_for_provider(
        self,
        *,
        provider_account_id: int,
    ) -> list[Payout]:
        statement = (
            select(Payout)
            .where(
                Payout.provider_account_id == provider_account_id,
                Payout.status == PayoutStatus.PENDING,
            )
            .order_by(Payout.created_at, Payout.id)
            .with_for_update()
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def claim_ready_for_provider(
        self,
        *,
        provider_account_id: int,
    ) -> list[Payout]:
        statement = (
            select(Payout)
            .where(
                Payout.provider_account_id == provider_account_id,
                Payout.status == PayoutStatus.READY,
                Payout.request_idempotency_key.is_(None),
            )
            .order_by(Payout.created_at, Payout.id)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def get_max_claimed_chain_nonce(self) -> int | None:
        statement = select(func.max(Payout.chain_nonce)).where(
            Payout.chain_nonce.is_not(None),
            Payout.status.in_((PayoutStatus.PENDING, PayoutStatus.SENT, PayoutStatus.FAILED)),
        )
        value = await self._session.scalar(statement)
        if value is None:
            return None
        return int(value)


__all__ = [
    "PayoutExecutionRepository",
]
