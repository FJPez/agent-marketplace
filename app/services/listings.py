"""The catalogue's one answer to whether a listing can be invoked, and what with."""

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccessMode, ServiceLifecycle
from app.core.errors import NotFoundError
from app.core.json_types import JsonObject, to_json_object
from app.db.models import ListingPrice, ProviderUpstream, Service, ServiceEndpoint
from app.services import moderation


@dataclass(frozen=True, slots=True)
class ListingUpstream:
    base_url: str
    path: str
    http_method: str


@dataclass(frozen=True, slots=True)
class ListingPriceTerms:
    """A listing's current price version, as stored."""

    id: int
    version: int
    amount: int
    asset: str
    network: str
    pay_to: str
    max_timeout_seconds: int
    fee_bps: int


@dataclass(frozen=True, slots=True)
class InvokableListing:
    """What the invoke path needs of a listing, read in one statement.

    A snapshot: a moderation action or a provider edit committed after the load does not
    change it.
    """

    # The listing is its service endpoint (spec D13).
    listing_id: int
    service_id: int
    provider_account_id: int
    upstream: ListingUpstream
    # None for a free listing. A paid listing's version is returned as stored, even when
    # the marketplace's payment settings have changed since it was created (see
    # provider_endpoints.is_on_current_terms): the invoke path decides what to do then.
    price: ListingPriceTerms | None
    timeout_seconds: int
    supports_idempotency: bool
    response_content_type: str
    # Validated per request in the worker pool with validate_request_body(pool=...,
    # schema=listing.request_schema, body=...) (app.core.request_validation); never
    # compiled here.
    request_schema: JsonObject


async def load_invokable_listing(
    *,
    session: AsyncSession,
    service_slug: str,
    endpoint_key: str,
) -> InvokableListing:
    """Load the listing invoked at /v1/invoke/{service_slug}/{endpoint_key}.

    The one definition of "can be invoked": the service is active and neither suspended
    nor delisted, the endpoint is enabled and has an upstream, and a paid endpoint has a
    current price version. Anything else is NotFoundError, so a consumer cannot tell a
    moderated or unfinished listing from a missing one. Call it with no transaction
    open: it ends its read transaction before returning, because the invoke path calls
    the facilitator or the provider next.
    """
    statement = (
        select(ServiceEndpoint, Service.provider_account_id, ProviderUpstream, ListingPrice)
        .join(Service, Service.id == ServiceEndpoint.service_id)
        .join(ProviderUpstream, ProviderUpstream.endpoint_id == ServiceEndpoint.id)
        .outerjoin(ListingPrice, ListingPrice.id == ServiceEndpoint.current_price_id)
        .where(
            Service.slug == service_slug,
            ServiceEndpoint.key == endpoint_key,
            Service.lifecycle == ServiceLifecycle.ACTIVE,
            moderation.is_clear(),
            ServiceEndpoint.is_enabled.is_(True),
            or_(
                ServiceEndpoint.access_mode == AccessMode.FREE,
                ServiceEndpoint.current_price_id.is_not(None),
            ),
        )
    )
    try:
        row = (await session.execute(statement)).one_or_none()
        if row is None:
            raise NotFoundError("listing not found")
        endpoint, provider_account_id, upstream, price = row
        return InvokableListing(
            listing_id=endpoint.id,
            service_id=endpoint.service_id,
            provider_account_id=provider_account_id,
            upstream=ListingUpstream(
                base_url=upstream.base_url,
                path=upstream.path,
                http_method=upstream.http_method,
            ),
            price=None
            if price is None
            else ListingPriceTerms(
                id=price.id,
                version=price.version,
                amount=price.amount,
                asset=price.asset,
                network=price.network,
                pay_to=price.pay_to,
                max_timeout_seconds=price.max_timeout_seconds,
                fee_bps=price.fee_bps,
            ),
            timeout_seconds=endpoint.timeout_seconds,
            supports_idempotency=endpoint.supports_idempotency,
            response_content_type=endpoint.response_content_type,
            request_schema=to_json_object(endpoint.request_schema),
        )
    finally:
        # Built before this: ending the transaction expires the rows read.
        await session.rollback()
