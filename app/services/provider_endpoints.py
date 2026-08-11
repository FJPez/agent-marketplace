from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.config import Settings
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.core.json_types import JsonObject, to_json_value
from app.core.service_fields import (
    normalize_endpoint_summary,
    normalize_http_method,
    normalize_service_description,
    normalize_service_name,
    normalize_slug,
    normalize_upstream_path,
    validate_endpoint_timeout,
)
from app.core.upstream_targets import UnsafeUpstreamTargetError, validate_upstream_base_url
from app.db.errors import is_unique_violation
from app.db.models.pricing_model import PricingModel
from app.db.models.provider_upstream import ProviderUpstream
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.services import service_access
from app.services.moderation_service import ModerationService, ServiceUnavailableError
from app.services.revision_service import RevisionService, UpdateImpact

ENDPOINT_UPDATE_FIELDS = frozenset(
    {
        "name",
        "summary",
        "description",
        "access_mode",
        "request_schema",
        "response_schema",
        "timeout_seconds",
        "is_enabled",
        "pricing",
    },
)
PRICING_FIELDS = frozenset({"pricing_type", "amount_minor", "currency"})


@dataclass(frozen=True)
class ParsedPricing:
    pricing_type: PricingModelType
    amount_minor: int | None
    currency: str | None


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
    pricing: dict[str, object] | None,
) -> ServiceEndpoint:
    try:
        normalized_key = normalize_slug(key)
        normalized_name = normalize_service_name(name)
        normalized_summary = normalize_endpoint_summary(summary)
        normalized_description = normalize_service_description(description)
        normalized_timeout = validate_endpoint_timeout(timeout_seconds)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    parsed_pricing = _parse_pricing(pricing)
    pricing_plan = _plan_pricing(access_mode=access_mode, parsed=parsed_pricing)

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
        pricing=None,
        upstream=None,
    )
    session.add(endpoint)
    try:
        await session.flush()
        if pricing_plan is not None:
            pricing_row = PricingModel(
                endpoint_id=endpoint.id,
                pricing_type=pricing_plan.pricing_type,
                amount_minor=pricing_plan.amount_minor,
                currency=pricing_plan.currency,
            )
            session.add(pricing_row)
            endpoint.pricing = pricing_row
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
    updates: dict[str, object],
) -> ServiceEndpoint:
    if not updates:
        raise InvalidInputError("at least one field must be provided")

    unknown_fields = set(updates) - ENDPOINT_UPDATE_FIELDS
    if unknown_fields:
        unknown_field = sorted(unknown_fields)[0]
        raise InvalidInputError(f"unknown update field: {unknown_field}")

    now = datetime.now(UTC)

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

    update_fields: dict[str, object] = {}
    revision_fields: dict[str, object] = {}
    target_access_mode = endpoint.access_mode
    try:
        if "name" in updates:
            name = updates["name"]
            if name is None:
                raise InvalidInputError("name cannot be null")
            if not isinstance(name, str):
                raise InvalidInputError("name must be a string")
            update_fields["name"] = normalize_service_name(name)
            revision_fields["name"] = name
        if "summary" in updates:
            summary = updates["summary"]
            if summary is not None and not isinstance(summary, str):
                raise InvalidInputError("summary must be a string")
            update_fields["summary"] = normalize_endpoint_summary(summary)
            revision_fields["summary"] = summary
        if "description" in updates:
            description = updates["description"]
            if description is not None and not isinstance(description, str):
                raise InvalidInputError("description must be a string")
            update_fields["description"] = normalize_service_description(description)
            revision_fields["description"] = description
        if "access_mode" in updates:
            access_mode = updates["access_mode"]
            if access_mode is None:
                raise InvalidInputError("access_mode cannot be null")
            if not isinstance(access_mode, AccessMode):
                raise InvalidInputError("access_mode must be a valid access mode")
            update_fields["access_mode"] = access_mode
            revision_fields["access_mode"] = access_mode
            target_access_mode = access_mode
        if "request_schema" in updates:
            request_schema = to_json_value(updates["request_schema"])
            if request_schema is None:
                raise InvalidInputError("request_schema cannot be null")
            if not isinstance(request_schema, dict):
                raise InvalidInputError("request_schema must be an object")
            update_fields["request_schema"] = request_schema
            revision_fields["request_schema"] = request_schema
        if "response_schema" in updates:
            response_schema = to_json_value(updates["response_schema"])
            if response_schema is None:
                raise InvalidInputError("response_schema cannot be null")
            if not isinstance(response_schema, dict):
                raise InvalidInputError("response_schema must be an object")
            update_fields["response_schema"] = response_schema
            revision_fields["response_schema"] = response_schema
        if "timeout_seconds" in updates:
            timeout_seconds = updates["timeout_seconds"]
            if timeout_seconds is None:
                raise InvalidInputError("timeout_seconds cannot be null")
            if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
                raise InvalidInputError("timeout_seconds must be an integer")
            update_fields["timeout_seconds"] = validate_endpoint_timeout(timeout_seconds)
            revision_fields["timeout_seconds"] = timeout_seconds
        if "is_enabled" in updates:
            is_enabled = updates["is_enabled"]
            if is_enabled is None:
                raise InvalidInputError("is_enabled cannot be null")
            if not isinstance(is_enabled, bool):
                raise InvalidInputError("is_enabled must be a boolean")
            update_fields["is_enabled"] = is_enabled
            revision_fields["is_enabled"] = is_enabled
        if "pricing" in updates:
            revision_fields["pricing"] = updates["pricing"]
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc

    parsed_pricing = _parse_pricing(updates["pricing"]) if "pricing" in updates else None
    pricing_plan = _plan_pricing(access_mode=target_access_mode, parsed=parsed_pricing)

    await _ensure_endpoint_update_allowed(
        session=session,
        service=service,
        revision_fields=revision_fields,
    )

    if service.lifecycle is ServiceLifecycle.ACTIVE:
        _ensure_active_endpoint_pricing_valid(
            access_mode=target_access_mode,
            plan=pricing_plan,
            current_pricing=endpoint.pricing,
        )

    for attribute_name, value in update_fields.items():
        setattr(endpoint, attribute_name, value)
    endpoint.updated_at = now

    await _apply_pricing_plan(endpoint, plan=pricing_plan, session=session, now=now)

    if service.lifecycle is ServiceLifecycle.ACTIVE:
        await RevisionService(session).create_revision_if_material_endpoint_update(
            service,
            update_fields=revision_fields,
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

    try:
        normalized_path = normalize_upstream_path(path)
        normalized_http_method = normalize_http_method(http_method)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    try:
        validated_base_url = validate_upstream_base_url(base_url, settings=settings)
    except UnsafeUpstreamTargetError as exc:
        raise InvalidInputError(str(exc)) from exc

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
            selectinload(ServiceEndpoint.pricing),
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
    revision_fields: dict[str, object],
) -> None:
    if service.lifecycle is ServiceLifecycle.DRAFT:
        return
    if service.lifecycle is ServiceLifecycle.ACTIVE:
        if RevisionService.classify_endpoint_update(revision_fields) is not UpdateImpact.MATERIAL:
            return
        try:
            await ModerationService(session).ensure_service_publishable(service.id)
        except ServiceUnavailableError as exc:
            raise InvalidStateError(f"service is {exc.state.value}") from exc
        return
    raise InvalidStateError("service is not mutable outside draft")


def _ensure_active_endpoint_pricing_valid(
    *,
    access_mode: AccessMode,
    plan: ParsedPricing | None,
    current_pricing: PricingModel | None,
) -> None:
    if access_mode is not AccessMode.PAID:
        return
    target: ParsedPricing | PricingModel | None = plan
    if target is None and (
        current_pricing is not None
        and current_pricing.pricing_type is PricingModelType.FIXED_PER_CALL
    ):
        target = current_pricing
    if (
        target is None
        or target.pricing_type is not PricingModelType.FIXED_PER_CALL
        or target.amount_minor is None
        or target.currency is None
    ):
        raise InvalidInputError(
            "active paid endpoints must define fixed_per_call pricing",
        )


def _plan_pricing(
    *,
    access_mode: AccessMode,
    parsed: ParsedPricing | None,
) -> ParsedPricing | None:
    if access_mode is AccessMode.FREE:
        if parsed is not None and parsed.pricing_type is not PricingModelType.FREE:
            raise InvalidInputError("free endpoints must use free pricing")
        return ParsedPricing(
            pricing_type=PricingModelType.FREE,
            amount_minor=None,
            currency=None,
        )

    if parsed is None:
        return None
    if parsed.pricing_type is not PricingModelType.FIXED_PER_CALL:
        raise InvalidInputError("paid endpoints must use fixed_per_call pricing")
    return parsed


async def _apply_pricing_plan(
    endpoint: ServiceEndpoint,
    *,
    plan: ParsedPricing | None,
    session: AsyncSession,
    now: datetime,
) -> None:
    current_pricing = endpoint.pricing

    if plan is None:
        if current_pricing is not None and current_pricing.pricing_type is PricingModelType.FREE:
            endpoint.pricing = None
            await session.delete(current_pricing)
        return

    if current_pricing is not None:
        current_pricing.pricing_type = plan.pricing_type
        current_pricing.amount_minor = plan.amount_minor
        current_pricing.currency = plan.currency
        current_pricing.updated_at = now
        return

    new_pricing = PricingModel(
        endpoint_id=endpoint.id,
        pricing_type=plan.pricing_type,
        amount_minor=plan.amount_minor,
        currency=plan.currency,
    )
    session.add(new_pricing)
    endpoint.pricing = new_pricing


def _parse_pricing(pricing: object) -> ParsedPricing | None:
    if pricing is None:
        return None
    if not isinstance(pricing, dict):
        raise InvalidInputError("pricing must be an object or null")

    fields: dict[str, object] = {}
    for field_name, field_value in pricing.items():
        if not isinstance(field_name, str):
            raise InvalidInputError(f"unknown pricing field: {field_name}")
        fields[field_name] = field_value

    unknown_fields = set(fields) - PRICING_FIELDS
    if unknown_fields:
        unknown_field = sorted(unknown_fields)[0]
        raise InvalidInputError(f"unknown pricing field: {unknown_field}")

    pricing_type = _parse_pricing_type(fields)
    amount_minor = _parse_amount_minor(fields)
    currency = _parse_currency(fields)

    if pricing_type is PricingModelType.FREE:
        if amount_minor is not None or currency is not None:
            raise InvalidInputError("free pricing cannot include amount_minor or currency")
    elif amount_minor is None or currency is None:
        raise InvalidInputError("fixed_per_call pricing requires amount_minor and currency")

    return ParsedPricing(
        pricing_type=pricing_type,
        amount_minor=amount_minor,
        currency=currency,
    )


def _parse_pricing_type(fields: dict[str, object]) -> PricingModelType:
    raw_pricing_type = fields.get("pricing_type")
    if isinstance(raw_pricing_type, PricingModelType):
        return raw_pricing_type
    if isinstance(raw_pricing_type, str):
        try:
            return PricingModelType(raw_pricing_type)
        except ValueError as exc:
            raise InvalidInputError(
                "pricing_type must be one of: free, fixed_per_call",
            ) from exc
    raise InvalidInputError("pricing_type must be one of: free, fixed_per_call")


def _parse_amount_minor(fields: dict[str, object]) -> int | None:
    amount_minor = fields.get("amount_minor")
    if amount_minor is None:
        return None
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise InvalidInputError("amount_minor must be a positive integer")
    return amount_minor


def _parse_currency(fields: dict[str, object]) -> str | None:
    currency = fields.get("currency")
    if currency is None:
        return None
    if not isinstance(currency, str) or len(currency) != 3 or currency != currency.upper():
        raise InvalidInputError("currency must be a 3-letter uppercase currency code")
    return currency
