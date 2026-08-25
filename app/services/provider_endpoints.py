from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.config import Settings
from app.core.enums import AccessMode, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.core.upstream_targets import validate_upstream_base_url
from app.db.errors import is_unique_violation
from app.db.models.endpoint_price import EndpointPrice
from app.db.models.provider_upstream import ProviderUpstream
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.pricing import FixedPrice
from app.schemas.service import (
    EndpointCreateRequest,
    EndpointUpdateRequest,
    EndpointUpstreamRequest,
)
from app.services import service_access
from app.services.moderation_service import ModerationService, ServiceUnavailableError
from app.services.revision_service import RevisionService, UpdateImpact


async def create_endpoint(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
    request: EndpointCreateRequest,
) -> ServiceEndpoint:
    service = await service_access.load_owned_service_for_update(
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
        timeout_seconds=request.timeout_seconds,
        is_enabled=request.is_enabled,
        price=None,
        upstream=None,
    )
    session.add(endpoint)
    try:
        await session.flush()
        if request.pricing is not None:
            pricing_row = EndpointPrice(
                endpoint_id=endpoint.id,
                amount_minor=request.pricing.amount_minor,
                currency=request.pricing.currency,
            )
            session.add(pricing_row)
            endpoint.price = pricing_row
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if not is_unique_violation(exc):
            raise
        raise ConflictError("endpoint key already exists for this service") from exc

    return endpoint


async def get_endpoint(
    *,
    session: AsyncSession,
    account_id: int,
    endpoint_id: int,
) -> ServiceEndpoint:
    return await _load_owned_endpoint(
        session=session,
        account_id=account_id,
        endpoint_id=endpoint_id,
    )


async def update_endpoint(
    *,
    session: AsyncSession,
    account_id: int,
    endpoint_id: int,
    changes: EndpointUpdateRequest,
) -> ServiceEndpoint:
    locked_service_id = await service_access.lock_owned_service_by_endpoint(
        session=session,
        account_id=account_id,
        endpoint_id=endpoint_id,
    )
    service = await service_access.load_owned_service(
        session=session,
        account_id=account_id,
        service_id=locked_service_id,
    )
    endpoint = next(
        (candidate for candidate in service.endpoints if candidate.id == endpoint_id),
        None,
    )
    if endpoint is None:
        raise NotFoundError("endpoint not found")

    # access_mode is non-clearable (the schema rejects an explicit null), so
    # None can only mean the field was omitted.
    target_access_mode = (
        changes.access_mode if changes.access_mode is not None else endpoint.access_mode
    )
    price_specified = "pricing" in changes.model_fields_set

    if target_access_mode is AccessMode.FREE and price_specified and changes.pricing is not None:
        raise InvalidInputError("free endpoints cannot have a price")

    # Effective changes: supplied values that differ from the stored ones. They
    # drive no-op detection, the mutability gate, and revision classification,
    # so resending current values is not a change at all. Pricing lives in its
    # own table and is never assigned by setattr, so it is tracked separately.
    supplied = changes.model_dump(exclude_unset=True, exclude={"pricing"})
    column_changes = {
        name: value for name, value in supplied.items() if value != getattr(endpoint, name)
    }

    current_price = endpoint.price
    resulting_price: FixedPrice | None = None
    pricing_changed = False
    if price_specified:
        resulting_price = changes.pricing
        pricing_changed = True
    elif target_access_mode is AccessMode.FREE and current_price is not None:
        # Switching to FREE drops the row even though pricing was omitted.
        pricing_changed = True
    if pricing_changed and current_price is not None and resulting_price is not None:
        pricing_changed = (current_price.amount_minor, current_price.currency) != (
            resulting_price.amount_minor,
            resulting_price.currency,
        )
    elif pricing_changed:
        pricing_changed = (current_price is None) != (resulting_price is None)

    effective_changes: dict[str, object] = dict(column_changes)
    if pricing_changed:
        effective_changes["pricing"] = resulting_price

    if not effective_changes:
        return endpoint

    if target_access_mode is AccessMode.FREE:
        has_resulting_price = False
    elif pricing_changed:
        has_resulting_price = resulting_price is not None
    else:
        has_resulting_price = current_price is not None

    await _ensure_endpoint_update_allowed(
        session=session,
        service=service,
        changed_fields=effective_changes,
    )

    _ensure_active_paid_endpoint_priced(
        lifecycle=service.lifecycle,
        access_mode=target_access_mode,
        has_price=has_resulting_price,
    )

    # Stamped after the lock wait so the timestamp reflects when the row was
    # actually mutated.
    now = datetime.now(UTC)

    for attribute_name, value in column_changes.items():
        setattr(endpoint, attribute_name, value)
    endpoint.updated_at = now

    if pricing_changed:
        existing_price = endpoint.price
        if resulting_price is None:
            if existing_price is not None:
                endpoint.price = None
                await session.delete(existing_price)
        elif existing_price is None:
            pricing_row = EndpointPrice(
                endpoint_id=endpoint.id,
                amount_minor=resulting_price.amount_minor,
                currency=resulting_price.currency,
            )
            session.add(pricing_row)
            endpoint.price = pricing_row
        else:
            existing_price.amount_minor = resulting_price.amount_minor
            existing_price.currency = resulting_price.currency
            existing_price.updated_at = now

    if service.lifecycle is ServiceLifecycle.ACTIVE:
        await RevisionService(session).create_revision_if_material_endpoint_update(
            service,
            update_fields=effective_changes,
        )

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

    locked_service_id = await service_access.lock_owned_service_by_endpoint(
        session=session,
        account_id=account_id,
        endpoint_id=endpoint_id,
    )
    service = await service_access.load_owned_service(
        session=session,
        account_id=account_id,
        service_id=locked_service_id,
    )
    endpoint = next(
        (candidate for candidate in service.endpoints if candidate.id == endpoint_id),
        None,
    )
    if endpoint is None:
        raise NotFoundError("endpoint not found")

    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not mutable outside draft")

    now = datetime.now(UTC)
    upstream = endpoint.upstream
    if upstream is None:
        upstream = ProviderUpstream(
            endpoint_id=endpoint.id,
            base_url=validated_base_url,
            path=request.path,
            http_method=request.http_method,
            config=request.config,
        )
        session.add(upstream)
        endpoint.upstream = upstream
    else:
        upstream.base_url = validated_base_url
        upstream.path = request.path
        upstream.http_method = request.http_method
        upstream.config = request.config
        upstream.updated_at = now

    await session.commit()


async def _load_owned_endpoint(
    *,
    session: AsyncSession,
    account_id: int,
    endpoint_id: int,
) -> ServiceEndpoint:
    statement = (
        select(ServiceEndpoint)
        .join(Service)
        .options(
            joinedload(ServiceEndpoint.service),
            selectinload(ServiceEndpoint.price),
            selectinload(ServiceEndpoint.upstream),
        )
        .execution_options(populate_existing=True)
        .where(
            ServiceEndpoint.id == endpoint_id,
            Service.provider_account_id == account_id,
        )
    )
    endpoint = await session.scalar(statement)
    if endpoint is None:
        raise NotFoundError("endpoint not found")
    return endpoint


async def _ensure_endpoint_update_allowed(
    *,
    session: AsyncSession,
    service: Service,
    changed_fields: Collection[str],
) -> None:
    if service.lifecycle is ServiceLifecycle.DRAFT:
        return
    if service.lifecycle is ServiceLifecycle.ACTIVE:
        if RevisionService.classify_endpoint_update(changed_fields) is not UpdateImpact.MATERIAL:
            return
        try:
            await ModerationService(session).ensure_service_publishable(service.id)
        except ServiceUnavailableError as exc:
            raise InvalidStateError(f"service is {exc.state.value}") from exc
        return
    raise InvalidStateError("service is not mutable outside draft")


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
