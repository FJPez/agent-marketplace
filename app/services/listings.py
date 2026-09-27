"""The catalogue's one answer to whether a listing can be invoked, and what with."""

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccessMode, ServiceLifecycle
from app.core.errors import NotFoundError
from app.core.json_types import JsonObject, to_json_object
from app.db.models import (
    ListingPrice,
    ProviderSigningSecret,
    ProviderUpstream,
    Service,
    ServiceEndpoint,
)
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
    nor delisted, its provider has a signing secret for phase 1 to sign the forwarded
    request with, the endpoint is enabled and has an upstream, and a paid endpoint has a
    current price version. Anything else is NotFoundError, so a consumer cannot tell a
    moderated or unfinished listing from a missing one.

    `session` must have no transaction open, and ends this call with none: phase 1 calls
    the facilitator or the provider next, and must not do so with a transaction, let
    alone the caller's own, left open. Raises RuntimeError, a programming error rather
    than a NotFoundError, if the caller passes a session with a transaction already
    open; it does not touch that transaction, so the caller's work is left exactly as
    it was.
    """
    if session.in_transaction():
        msg = "load_invokable_listing needs a session with no transaction open"
        raise RuntimeError(msg)

    has_signing_secret = (
        select(ProviderSigningSecret.account_id)
        .where(ProviderSigningSecret.account_id == Service.provider_account_id)
        .exists()
    )
    statement = (
        select(
            ServiceEndpoint.id.label("listing_id"),
            ServiceEndpoint.service_id,
            Service.provider_account_id,
            ServiceEndpoint.timeout_seconds,
            ServiceEndpoint.supports_idempotency,
            ServiceEndpoint.response_content_type,
            ServiceEndpoint.request_schema,
            ProviderUpstream.base_url,
            ProviderUpstream.path,
            ProviderUpstream.http_method,
            ListingPrice.id.label("price_id"),
            ListingPrice.version,
            ListingPrice.amount,
            ListingPrice.asset,
            ListingPrice.network,
            ListingPrice.pay_to,
            ListingPrice.max_timeout_seconds,
            ListingPrice.fee_bps,
        )
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
            # Phase 1 signs every forwarded request. A service published (by the seed
            # script, say) whose provider never got a signing secret is not invokable,
            # rather than failing only after a consumer has paid. The secret itself
            # never leaves this query: InvokableListing carries no such field, so it
            # cannot reach a log through its repr.
            has_signing_secret,
        )
    )
    try:
        row = (await session.execute(statement)).one_or_none()
        if row is None:
            raise NotFoundError("listing not found")
        return InvokableListing(
            listing_id=row.listing_id,
            service_id=row.service_id,
            provider_account_id=row.provider_account_id,
            upstream=ListingUpstream(
                base_url=row.base_url,
                path=row.path,
                http_method=row.http_method,
            ),
            price=None
            if row.price_id is None
            else ListingPriceTerms(
                id=row.price_id,
                version=row.version,
                amount=row.amount,
                asset=row.asset,
                network=row.network,
                pay_to=row.pay_to,
                max_timeout_seconds=row.max_timeout_seconds,
                fee_bps=row.fee_bps,
            ),
            timeout_seconds=row.timeout_seconds,
            supports_idempotency=row.supports_idempotency,
            response_content_type=row.response_content_type,
            request_schema=to_json_object(row.request_schema),
        )
    finally:
        # A plain column select loads no ORM instances, so there is nothing to expire:
        # this only ends the read transaction the select above opened.
        await session.rollback()
