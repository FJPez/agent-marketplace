from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PricingModelType
from app.db.models.pricing_model import PricingModel
from app.db.models.service_endpoint import ServiceEndpoint


class PricingModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def upsert_free(self, endpoint: ServiceEndpoint) -> PricingModel:
        pricing = endpoint.pricing
        if pricing is None:
            pricing = PricingModel(
                endpoint_id=endpoint.id,
                pricing_type=PricingModelType.FREE,
            )
            self._session.add(pricing)
            endpoint.pricing = pricing
            return pricing

        pricing.pricing_type = PricingModelType.FREE
        pricing.amount_minor = None
        pricing.currency = None
        pricing.updated_at = datetime.now(UTC)
        return pricing

    def upsert_fixed_per_call(
        self,
        endpoint: ServiceEndpoint,
        *,
        amount_minor: int,
        currency: str,
    ) -> PricingModel:
        pricing = endpoint.pricing
        if pricing is None:
            pricing = PricingModel(
                endpoint_id=endpoint.id,
                pricing_type=PricingModelType.FIXED_PER_CALL,
                amount_minor=amount_minor,
                currency=currency,
            )
            self._session.add(pricing)
            endpoint.pricing = pricing
            return pricing

        pricing.pricing_type = PricingModelType.FIXED_PER_CALL
        pricing.amount_minor = amount_minor
        pricing.currency = currency
        pricing.updated_at = datetime.now(UTC)
        return pricing

    async def delete_for_endpoint(self, endpoint: ServiceEndpoint) -> None:
        pricing = endpoint.pricing
        if pricing is None:
            return
        endpoint.pricing = None
        await self._session.delete(pricing)
