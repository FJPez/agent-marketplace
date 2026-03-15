from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PricingModelType
from app.db.models import Quote


class QuoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str,
        request_hash: str,
        pricing_type: PricingModelType,
        amount_minor: int | None,
        currency: str | None,
        service_revision_id: int | None,
        service_change_token: str | None,
        expires_at: datetime,
    ) -> Quote:
        quote = Quote(
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key=endpoint_key,
            request_hash=request_hash,
            pricing_type=pricing_type,
            amount_minor=amount_minor,
            currency=currency,
            service_revision_id=service_revision_id,
            service_change_token=service_change_token,
            expires_at=expires_at,
        )
        self._session.add(quote)
        return quote

    async def get(self, *, quote_id: int) -> Quote | None:
        statement = select(Quote).where(Quote.id == quote_id)
        return await self._session.scalar(statement)
