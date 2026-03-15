from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import PricingModelType
from app.core.request_hash import hash_request_body
from app.db.models import Quote, ServiceEndpoint
from app.repositories.quote_repo import QuoteRepository
from app.repositories.service_repo import ServiceRepository


class QuoteNotFoundError(Exception):
    pass


class QuoteMismatchError(Exception):
    pass


class QuoteExpiredError(Exception):
    pass


class QuoteStaleError(Exception):
    pass


class QuoteService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._quote_repo = QuoteRepository(session)
        self._service_repo = ServiceRepository(session)

    async def create_quote(
        self,
        *,
        service_id_or_slug: str,
        endpoint_key: str,
        payload: dict[str, object],
    ) -> Quote:
        service = await self._service_repo.get_public(service_id_or_slug=service_id_or_slug)
        if service is None:
            raise QuoteNotFoundError("service not found")

        endpoint = self._get_enabled_endpoint(service.endpoints, endpoint_key=endpoint_key)
        if endpoint is None:
            raise QuoteNotFoundError("endpoint not found")

        request_hash = hash_request_body(payload)
        ttl = timedelta(seconds=get_settings().quote_ttl_seconds)
        created_at = datetime.now(UTC)
        expires_at = created_at + ttl
        quote = self._quote_repo.add(
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key=endpoint.key,
            request_hash=request_hash,
            pricing_type=self._get_pricing_type(endpoint),
            amount_minor=None if endpoint.pricing is None else endpoint.pricing.amount_minor,
            currency=None if endpoint.pricing is None else endpoint.pricing.currency,
            service_revision_id=service.current_revision_id,
            service_change_token=service.current_change_token,
            expires_at=expires_at,
        )
        await self._session.commit()
        await self._session.refresh(quote)
        return quote

    async def validate_quote(
        self,
        *,
        quote_id: int,
        payload: dict[str, object],
        now: datetime | None = None,
    ) -> Quote:
        quote = await self._quote_repo.get(quote_id=quote_id)
        if quote is None:
            raise QuoteNotFoundError("quote not found")

        resolved_now = now or datetime.now(UTC)
        if quote.expires_at <= resolved_now:
            raise QuoteExpiredError("quote has expired")

        if quote.request_hash != hash_request_body(payload):
            raise QuoteMismatchError("request hash does not match quote")

        service = await self._service_repo.get_public(service_id_or_slug=str(quote.service_id))
        if service is None:
            raise QuoteStaleError("quote no longer matches current service state")

        endpoint = self._get_enabled_endpoint(service.endpoints, endpoint_key=quote.endpoint_key)
        if endpoint is None:
            raise QuoteStaleError("quote no longer matches current service state")

        if (
            quote.service_revision_id != service.current_revision_id
            or quote.service_change_token != service.current_change_token
        ):
            raise QuoteStaleError("quote no longer matches current service state")

        return quote

    def _get_enabled_endpoint(
        self,
        endpoints: list[ServiceEndpoint],
        *,
        endpoint_key: str,
    ) -> ServiceEndpoint | None:
        for endpoint in endpoints:
            if endpoint.key == endpoint_key and endpoint.is_enabled:
                return endpoint
        return None

    def _get_pricing_type(self, endpoint: ServiceEndpoint) -> PricingModelType:
        if endpoint.pricing is not None:
            return endpoint.pricing.pricing_type
        return PricingModelType.FREE
