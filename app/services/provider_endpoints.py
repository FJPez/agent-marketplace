from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.enums import AccessMode, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError
from app.core.upstream_targets import validate_upstream_base_url
from app.db.errors import is_unique_violation, unique_violation_constraint
from app.db.models.listing_price import LISTING_PRICE_VERSION_CONSTRAINT, ListingPrice
from app.db.models.provider_upstream import ProviderUpstream
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.service import (
    EndpointCreateRequest,
    EndpointUpdateRequest,
    EndpointUpstreamRequest,
)
from app.services import moderation, revisions, service_access
from app.services.moderation import ServiceUnavailableError
from app.services.revisions import UpdateImpact


async def create_endpoint(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
    service_id: int,
    request: EndpointCreateRequest,
) -> ServiceEndpoint:
    new_price = (
        None
        if request.price is None
        else build_price_version(settings=settings, amount=request.price.amount)
    )
    service = await service_access.lock_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not mutable outside draft")

    endpoint = ServiceEndpoint(
        service_id=service.id,
        key=request.key,
        name=request.name,
        summary=request.summary,
        description=request.description,
        access_mode=request.access_mode,
        request_schema=request.request_schema,
        response_schema=request.response_schema,
        response_content_type=request.response_content_type,
        timeout_seconds=request.timeout_seconds,
        supports_idempotency=request.supports_idempotency,
        is_enabled=request.is_enabled,
        current_price=None,
        upstream=None,
    )
    session.add(endpoint)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if not is_unique_violation(exc):
            raise
        raise ConflictError("endpoint key already exists for this service") from exc
    if new_price is not None:
        await put_price_on_sale(session=session, endpoint=endpoint, price=new_price)
    await session.commit()
    return endpoint


async def update_endpoint(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
    endpoint_id: int,
    changes: EndpointUpdateRequest,
) -> ServiceEndpoint:
    await service_access.lock_owned_service_by_endpoint(
        session=session,
        account_id=account_id,
        endpoint_id=endpoint_id,
    )
    endpoint = await service_access.load_owned_endpoint(
        session=session,
        account_id=account_id,
        endpoint_id=endpoint_id,
    )
    service = endpoint.service

    # access_mode is non-clearable (the schema rejects an explicit null), so
    # None can only mean the field was omitted.
    target_access_mode = (
        changes.access_mode if changes.access_mode is not None else endpoint.access_mode
    )
    if target_access_mode is AccessMode.FREE and changes.price is not None:
        raise InvalidInputError("free endpoints cannot have a price")

    # Effective changes: supplied values that differ from the stored ones. They
    # drive no-op detection, the mutability gate, and revision classification,
    # so resending current values is not a change at all. The price lives in
    # its own table and is never assigned by setattr, so it is tracked separately.
    supplied = changes.model_dump(exclude_unset=True, exclude={"price"})
    column_changes = {
        name: value for name, value in supplied.items() if value != getattr(endpoint, name)
    }

    current_price = endpoint.current_price
    current_amount = None if current_price is None else current_price.amount
    price_supplied = "price" in changes.model_fields_set
    if target_access_mode is AccessMode.FREE:
        # Switching to FREE drops the current price even when price was omitted.
        resulting_amount = None
    elif price_supplied:
        resulting_amount = None if changes.price is None else changes.price.amount
    else:
        resulting_amount = current_amount
    # Resending the current amount moves the listing onto the payment terms in
    # force when they have changed since its version was created. Without a
    # treasury no version can be created, so the resend stays a no-op; omitting
    # the price never re-stamps it.
    price_changed = resulting_amount != current_amount or (
        price_supplied
        and current_price is not None
        and settings.treasury_address is not None
        and not is_on_current_terms(current_price, settings=settings)
    )

    effective_changes: dict[str, object] = dict(column_changes)
    if price_changed:
        effective_changes["price"] = resulting_amount

    if not effective_changes:
        return endpoint

    impact = revisions.classify_endpoint_update(effective_changes)
    await _ensure_endpoint_update_allowed(
        session=session,
        service=service,
        impact=impact,
    )

    _ensure_active_paid_endpoint_priced(
        lifecycle=service.lifecycle,
        access_mode=target_access_mode,
        has_price=resulting_amount is not None,
    )
    new_price = (
        None
        if not price_changed or resulting_amount is None
        else build_price_version(settings=settings, amount=resulting_amount)
    )

    for attribute_name, value in column_changes.items():
        setattr(endpoint, attribute_name, value)
    # Stamped after the lock wait so the timestamp reflects when the row was
    # actually mutated.
    endpoint.updated_at = datetime.now(UTC)

    if new_price is not None:
        await put_price_on_sale(session=session, endpoint=endpoint, price=new_price)
    elif price_changed:
        # Earlier versions stay: purchases and revisions still refer to them.
        endpoint.current_price = None

    if service.lifecycle is ServiceLifecycle.ACTIVE and impact is UpdateImpact.MATERIAL:
        # The contract snapshot covers every endpoint and its price, so the full
        # graph is loaded here and only on the material path - the update itself
        # needs the target endpoint alone.
        graph = await service_access.load_owned_service(
            session=session,
            account_id=account_id,
            service_id=service.id,
        )
        await revisions.create_revision(session=session, service=graph)

    await session.commit()
    return endpoint


async def upsert_upstream(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
    endpoint_id: int,
    request: EndpointUpstreamRequest,
) -> None:
    try:
        # Resolves DNS - must run before the first query so no transaction or row
        # lock is held across the network I/O.
        validated_base_url = validate_upstream_base_url(str(request.base_url), settings=settings)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc

    await service_access.lock_owned_service_by_endpoint(
        session=session,
        account_id=account_id,
        endpoint_id=endpoint_id,
    )
    endpoint = await service_access.load_owned_endpoint(
        session=session,
        account_id=account_id,
        endpoint_id=endpoint_id,
    )
    service = endpoint.service

    upstream = endpoint.upstream
    if (
        upstream is not None
        and upstream.base_url == validated_base_url
        and upstream.path == request.path
        and upstream.http_method == request.http_method
    ):
        return

    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not mutable outside draft")

    now = datetime.now(UTC)
    if upstream is None:
        upstream = ProviderUpstream(
            endpoint_id=endpoint.id,
            base_url=validated_base_url,
            path=request.path,
            http_method=request.http_method,
        )
        session.add(upstream)
        endpoint.upstream = upstream
    else:
        upstream.base_url = validated_base_url
        upstream.path = request.path
        upstream.http_method = request.http_method
        upstream.updated_at = now

    await session.commit()


async def _ensure_endpoint_update_allowed(
    *,
    session: AsyncSession,
    service: Service,
    impact: UpdateImpact,
) -> None:
    if service.lifecycle is ServiceLifecycle.DRAFT:
        return
    if service.lifecycle is ServiceLifecycle.ACTIVE:
        if impact is not UpdateImpact.MATERIAL:
            return
        try:
            await moderation.ensure_service_publishable(session=session, service_id=service.id)
        except ServiceUnavailableError as exc:
            raise InvalidStateError(f"service is {exc.state.value}") from exc
        return
    raise InvalidStateError("service is not mutable outside draft")


def build_price_version(*, settings: Settings, amount: int) -> ListingPrice:
    """Check a provider's amount and build its price version on the current terms.

    The version is not added to the session: `put_price_on_sale` numbers it and
    attaches it to its endpoint once every other check has passed.
    """
    if amount < settings.min_price_amount:
        raise InvalidInputError(
            f"price amount must be at least {settings.min_price_amount} atomic units "
            "of the payment asset",
        )
    if settings.treasury_address is None:
        raise InvalidStateError(
            "paid prices are unavailable until APP_TREASURY_ADDRESS is configured",
        )
    return ListingPrice(amount=amount, **_current_payment_terms(settings))


def is_on_current_terms(price: ListingPrice, *, settings: Settings) -> bool:
    """Whether `price` carries the payment terms a version created now would.

    The amount is not compared. Without a treasury no version can be created, so
    no version is on the current terms.
    """
    return all(
        getattr(price, term) == value for term, value in _current_payment_terms(settings).items()
    )


def _current_payment_terms(settings: Settings) -> dict[str, str | int | None]:
    """The payment terms a new price version copies from the settings."""
    return {
        "asset": settings.payment_asset,
        "network": settings.payment_network,
        "pay_to": settings.treasury_address,
        "max_timeout_seconds": settings.payment_max_timeout_seconds,
        "fee_bps": settings.platform_fee_bps,
    }


async def put_price_on_sale(
    *,
    session: AsyncSession,
    endpoint: ServiceEndpoint,
    price: ListingPrice,
) -> None:
    """Store `price` as the endpoint's next version and make it the current price.

    The editing services hold the service row lock, so max + 1 is normally free;
    the unique (endpoint_id, version) key turns a writer without it (the demo
    seed) into a conflict instead of a silently reused version number. On any
    integrity error it rolls back the caller's whole transaction before raising.
    """
    latest_version = await session.scalar(
        select(func.max(ListingPrice.version)).where(ListingPrice.endpoint_id == endpoint.id),
    )
    price.endpoint_id = endpoint.id
    price.version = (latest_version or 0) + 1
    # Assigning the relationship of an endpoint in the session adds the version.
    endpoint.current_price = price
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if unique_violation_constraint(exc) != LISTING_PRICE_VERSION_CONSTRAINT:
            raise
        raise ConflictError("the endpoint's price changed concurrently; retry") from exc


def _ensure_active_paid_endpoint_priced(
    *,
    lifecycle: ServiceLifecycle,
    access_mode: AccessMode,
    has_price: bool,
) -> None:
    if lifecycle is not ServiceLifecycle.ACTIVE:
        return
    if access_mode is not AccessMode.PAID:
        return
    if not has_price:
        raise InvalidInputError("active paid endpoints must define a price")
