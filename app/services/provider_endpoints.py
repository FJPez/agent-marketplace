from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.config import Settings
from app.core.enums import AccessMode, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.core.json_types import JsonObject
from app.core.service_fields import (
    normalize_endpoint_summary,
    normalize_http_method,
    normalize_service_description,
    normalize_service_name,
    normalize_slug,
    normalize_upstream_path,
    validate_endpoint_timeout,
)
from app.core.upstream_targets import validate_upstream_base_url
from app.db.errors import is_unique_violation
from app.db.models.endpoint_price import EndpointPrice
from app.db.models.provider_upstream import ProviderUpstream
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.pricing import FixedPrice
from app.schemas.service import EndpointUpdateRequest
from app.services import service_access
from app.services.moderation_service import ModerationService, ServiceUnavailableError
from app.services.revision_service import RevisionService, UpdateImpact


async def create_endpoint(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
    key: str,
    name: str,
    summary: str | None,
    description: str | None,
    access_mode: AccessMode,
    request_schema: JsonObject,
    response_schema: JsonObject,
    timeout_seconds: int,
    is_enabled: bool,
    price: FixedPrice | None,
) -> ServiceEndpoint:
    try:
        normalized_key = normalize_slug(key)
        normalized_name = normalize_service_name(name)
        normalized_summary = normalize_endpoint_summary(summary)
        normalized_description = normalize_service_description(description)
        normalized_timeout = validate_endpoint_timeout(timeout_seconds)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    if access_mode is AccessMode.FREE and price is not None:
        raise InvalidInputError("free endpoints cannot have a price")

    service = await service_access.load_owned_service_for_update(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not mutable outside draft")

    endpoint = ServiceEndpoint(
        service_id=service.id,
        key=normalized_key,
        name=normalized_name,
        summary=normalized_summary,
        description=normalized_description,
        access_mode=access_mode,
        request_schema=request_schema,
        response_schema=response_schema,
        timeout_seconds=normalized_timeout,
        is_enabled=is_enabled,
        price=None,
        upstream=None,
    )
    session.add(endpoint)
    try:
        await session.flush()
        if price is not None:
            pricing_row = EndpointPrice(
                endpoint_id=endpoint.id,
                amount_minor=price.amount_minor,
                currency=price.currency,
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

    set_fields = changes.model_fields_set
    if "access_mode" in set_fields:
        if changes.access_mode is None:
            # Unreachable: the request schema rejects an explicit null here.
            raise InvalidInputError("access_mode cannot be null")
        target_access_mode = changes.access_mode
    else:
        target_access_mode = endpoint.access_mode
    price_specified = "pricing" in set_fields

    if target_access_mode is AccessMode.FREE and price_specified and changes.pricing is not None:
        raise InvalidInputError("free endpoints cannot have a price")

    # Effective changes: supplied fields whose target differs from the stored
    # value. They drive no-op detection, the mutability gate, and revision
    # classification, so resending current values is not a change at all.
    effective_changes: dict[str, object] = {}
    for attribute_name in set_fields - {"pricing"}:
        target = getattr(changes, attribute_name)
        if target != getattr(endpoint, attribute_name):
            effective_changes[attribute_name] = target

    pricing_change = _resolve_pricing_change(
        current=endpoint.price,
        requested=changes.pricing,
        price_specified=price_specified,
        target_access_mode=target_access_mode,
    )
    effective_changes.update(pricing_change)

    # Pricing lives in its own table and is never assigned by setattr.
    column_changes = {
        attribute_name: value
        for attribute_name, value in effective_changes.items()
        if attribute_name != "pricing"
    }

    if not effective_changes:
        return endpoint

    if target_access_mode is AccessMode.FREE:
        has_resulting_price = False
    elif pricing_change:
        has_resulting_price = pricing_change["pricing"] is not None
    else:
        has_resulting_price = endpoint.price is not None

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

    if pricing_change:
        resulting_price = pricing_change["pricing"]
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
    base_url: str,
    path: str,
    http_method: str,
    config: JsonObject,
) -> None:
    try:
        normalized_path = normalize_upstream_path(path)
        normalized_http_method = normalize_http_method(http_method)
        # Resolves DNS - must run before the first query so no transaction or row
        # lock is held across the network I/O.
        validated_base_url = validate_upstream_base_url(base_url, settings=settings)
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
            path=normalized_path,
            http_method=normalized_http_method,
            config=config,
        )
        session.add(upstream)
        endpoint.upstream = upstream
    else:
        upstream.base_url = validated_base_url
        upstream.path = normalized_path
        upstream.http_method = normalized_http_method
        upstream.config = config
        upstream.updated_at = now

    await session.commit()


def _resolve_pricing_change(
    *,
    current: EndpointPrice | None,
    requested: FixedPrice | None,
    price_specified: bool,
    target_access_mode: AccessMode,
) -> dict[str, FixedPrice | None]:
    """Resolve the resulting pricing state when it differs from the stored row.

    Returns a single-entry ``{"pricing": resulting}`` mapping when the pricing
    row must change, and an empty mapping when it must be left alone.
    """
    if price_specified:
        resulting = requested
    elif target_access_mode is AccessMode.FREE and current is not None:
        # Switching to FREE drops the row even though pricing was omitted.
        resulting = None
    else:
        return {}

    if (current is None) != (resulting is None):
        return {"pricing": resulting}
    if current is None or resulting is None:
        return {}
    if (current.amount_minor, current.currency) != (resulting.amount_minor, resulting.currency):
        return {"pricing": resulting}
    return {}


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
