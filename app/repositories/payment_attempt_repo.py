from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PaymentAttemptStatus
from app.db.models import PaymentAttempt


class PaymentAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        consumer_account_id: int,
        quote_id: int,
        invocation_id: int | None,
        idempotency_key: str,
        payment_identifier: str | None,
        status: PaymentAttemptStatus,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
        verify_outcome: dict[str, object] | None,
        settle_outcome: dict[str, object] | None,
        facilitator_reference: str | None,
    ) -> PaymentAttempt:
        attempt = PaymentAttempt(
            consumer_account_id=consumer_account_id,
            quote_id=quote_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            payment_identifier=payment_identifier,
            status=status,
            payment_requirement=payment_requirement,
            payment_payload=payment_payload,
            verify_outcome=verify_outcome,
            settle_outcome=settle_outcome,
            facilitator_reference=facilitator_reference,
        )
        self._session.add(attempt)
        return attempt

    async def get_by_payment_identifier(
        self,
        *,
        payment_identifier: str,
    ) -> PaymentAttempt | None:
        statement = select(PaymentAttempt).where(
            PaymentAttempt.payment_identifier == payment_identifier,
        )
        return await self._session.scalar(statement)

    async def get_by_quote_and_identifier(
        self,
        *,
        quote_id: int,
        payment_identifier: str,
    ) -> PaymentAttempt | None:
        statement = select(PaymentAttempt).where(
            PaymentAttempt.quote_id == quote_id,
            PaymentAttempt.payment_identifier == payment_identifier,
        )
        return await self._session.scalar(statement)

    async def get_by_invocation_id(
        self,
        *,
        invocation_id: int,
    ) -> PaymentAttempt | None:
        statement = select(PaymentAttempt).where(PaymentAttempt.invocation_id == invocation_id)
        return await self._session.scalar(statement)
