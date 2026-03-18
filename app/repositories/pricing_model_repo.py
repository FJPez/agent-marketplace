from datetime import UTC, datetime

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import NO_VALUE

from app.core.enums import PricingModelType
from app.db.models.pricing_model import PricingModel
from app.db.models.service_endpoint import ServiceEndpoint


def _get_loaded_pricing(endpoint: ServiceEndpoint) -> PricingModel | None:
    pricing = inspect(endpoint).attrs.pricing.loaded_value
    if pricing is NO_VALUE:
        return None
    if isinstance(pricing, PricingModel):
        return pricing
    return None


class PricingModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def upsert_free(self, endpoint: ServiceEndpoint) -> PricingModel:
        pricing = _get_loaded_pricing(endpoint)
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
        pricing = _get_loaded_pricing(endpoint)
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
        pricing = _get_loaded_pricing(endpoint)
        if pricing is None:
            return
        endpoint.pricing = None
        await self._session.delete(pricing)
