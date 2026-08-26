"""Quote creation and validation for priced service endpoints."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.config import Settings
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.core.errors import InvalidStateError, NotFoundError
from app.core.logging import (
    QUOTE_ID_FIELD,
    SERVICE_ID_FIELD,
    build_event_context,
    get_logger,
)
from app.core.request_hash import hash_request_body
from app.core.request_schema_validation import validate_request_payload
from app.db.models import Quote, Service, ServiceEndpoint
from app.schemas.quote import QuoteCreateRequest
from app.schemas.service_ref import PublicServiceRef
from app.services import moderation
from app.services.moderation import ServiceUnavailableError

logger = get_logger(__name__)


class QuoteMismatchError(InvalidStateError):
    """Replayed payload no longer hashes to the quoted request."""


class QuoteExpiredError(InvalidStateError):
    """Quote is past its expiry window."""


class QuoteStaleError(InvalidStateError):
    """Quote no longer describes the service contract it was issued against."""


async def create_quote(
    *,
    session: AsyncSession,
    settings: Settings,
    service_ref: PublicServiceRef,
    request: QuoteCreateRequest,
) -> Quote:
    """Price an enabled paid endpoint and bind the quote to the current service contract."""
    statement = select(Service).where(Service.lifecycle == ServiceLifecycle.ACTIVE)
    statement = statement.where(
        Service.id == service_ref.id
        if service_ref.id is not None
        else Service.slug == service_ref.slug
    )
    service = await session.scalar(statement)
    if service is None:
        raise NotFoundError("service not found")

    try:
        await moderation.ensure_service_available(session=session, service_id=service.id)
    except ServiceUnavailableError as exc:
        # Suspended and delisted services must stay indistinguishable from missing ones publicly.
        raise NotFoundError("service not found") from exc

    if service.current_revision_id is None or service.current_change_token is None:
        raise InvalidStateError("service contract is not quoteable")

    endpoint = await session.scalar(
        select(ServiceEndpoint)
        # One endpoint, one-to-one price: a join beats a second SELECT here.
        .options(joinedload(ServiceEndpoint.price))
        .where(
            ServiceEndpoint.service_id == service.id,
            ServiceEndpoint.key == request.endpoint_key,
            ServiceEndpoint.is_enabled.is_(True),
        ),
    )
    # A disabled endpoint is indistinguishable from a missing one for quoting.
    if endpoint is None:
        raise NotFoundError("endpoint not found")

    price = endpoint.price
    if endpoint.access_mode is not AccessMode.PAID or price is None:
        raise InvalidStateError("endpoint is not quoteable")

    validate_request_payload(
        payload=request.payload,
        request_schema=endpoint.request_schema,
    )

    now = datetime.now(UTC)
    quote = Quote(
        service_id=service.id,
        endpoint_id=endpoint.id,
        endpoint_key=endpoint.key,
        request_hash=hash_request_body(request.payload),
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=price.amount_minor,
        currency=price.currency,
        service_revision_id=service.current_revision_id,
        service_change_token=service.current_change_token,
        created_at=now,
        expires_at=now + timedelta(seconds=settings.quote_ttl_seconds),
    )
    session.add(quote)
    await session.commit()
    await session.refresh(quote)
    logger.info(
        "quote created",
        extra=build_event_context(
            "quote.created",
            **{
                SERVICE_ID_FIELD: quote.service_id,
                QUOTE_ID_FIELD: quote.id,
            },
        ),
    )
    return quote


async def validate_quote(
    *,
    session: AsyncSession,
    quote_id: int,
    payload: object,
) -> Quote:
    """Return the quote only while it still binds the payload to today's service contract."""
    quote = await session.get(Quote, quote_id)
    if quote is None:
        raise NotFoundError("quote not found")

    now = datetime.now(UTC)
    if quote.expires_at <= now:
        raise QuoteExpiredError("quote has expired")

    if quote.request_hash != hash_request_body(payload):
        raise QuoteMismatchError("request hash does not match quote")

    # The ACTIVE filter is what makes a service that left the catalogue read as stale.
    service = await session.scalar(
        select(Service).where(
            Service.id == quote.service_id,
            Service.lifecycle == ServiceLifecycle.ACTIVE,
        ),
    )
    if service is None:
        raise QuoteStaleError("quote no longer matches current service state")

    try:
        await moderation.ensure_service_available(session=session, service_id=service.id)
    except ServiceUnavailableError as exc:
        raise QuoteStaleError("quote no longer matches current service state") from exc

    if service.current_revision_id is None or service.current_change_token is None:
        raise QuoteStaleError("quote no longer matches current service state")

    enabled_endpoint_id = await session.scalar(
        select(ServiceEndpoint.id).where(
            ServiceEndpoint.service_id == service.id,
            ServiceEndpoint.key == quote.endpoint_key,
            ServiceEndpoint.is_enabled.is_(True),
        ),
    )
    if enabled_endpoint_id is None:
        raise QuoteStaleError("quote no longer matches current service state")

    if (
        quote.service_revision_id != service.current_revision_id
        or quote.service_change_token != service.current_change_token
    ):
        raise QuoteStaleError("quote no longer matches current service state")

    return quote
