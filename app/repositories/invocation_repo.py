from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccessMode, InvocationStatus
from app.db.models import Invocation


class InvocationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        consumer_account_id: int,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str,
        access_mode: AccessMode,
        quote_id: int | None,
        idempotency_key: str,
        request_hash: str,
        status: InvocationStatus,
        response_payload: dict[str, object] | None,
        upstream_status_code: int | None,
        error_message: str | None,
    ) -> Invocation:
        invocation = Invocation(
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key=endpoint_key,
            access_mode=access_mode,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status=status,
            response_payload=response_payload,
            upstream_status_code=upstream_status_code,
            error_message=error_message,
        )
        self._session.add(invocation)
        return invocation

    async def get_for_consumer(
        self,
        *,
        invocation_id: int,
        consumer_account_id: int,
    ) -> Invocation | None:
        statement = select(Invocation).where(
            Invocation.id == invocation_id,
            Invocation.consumer_account_id == consumer_account_id,
        )
        return await self._session.scalar(statement)

    async def list_for_consumer(self, *, consumer_account_id: int) -> list[Invocation]:
        statement = (
            select(Invocation)
            .where(Invocation.consumer_account_id == consumer_account_id)
            .order_by(desc(Invocation.created_at), desc(Invocation.id))
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def get_by_idempotency_key(
        self,
        *,
        consumer_account_id: int,
        idempotency_key: str,
    ) -> Invocation | None:
        statement = select(Invocation).where(
            Invocation.consumer_account_id == consumer_account_id,
            Invocation.idempotency_key == idempotency_key,
        )
        return await self._session.scalar(statement)
