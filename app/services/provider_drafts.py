from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError
from app.core.service_fields import (
    SERVICE_TAGS_MAX_COUNT,
    normalize_service_description,
    normalize_service_name,
    normalize_service_summary,
    normalize_slug,
    normalize_tag,
)
from app.db.errors import is_unique_violation
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.db.models.service_tag import ServiceTag
from app.services import service_access
from app.services.revision_service import RevisionService, UpdateImpact

SERVICE_UPDATE_FIELDS = frozenset({"name", "summary", "description"})


async def create_service(
    *,
    session: AsyncSession,
    account_id: int,
    slug: str,
    name: str,
    summary: str,
    description: str | None,
) -> Service:
    try:
        normalized_slug = normalize_slug(slug)
        normalized_name = normalize_service_name(name)
        normalized_summary = normalize_service_summary(summary)
        normalized_description = normalize_service_description(description)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc

    service = Service(
        provider_account_id=account_id,
        slug=normalized_slug,
        name=normalized_name,
        summary=normalized_summary,
        description=normalized_description,
        tags=[],
        endpoints=[],
    )
    session.add(service)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if not is_unique_violation(exc):
            raise
        raise ConflictError("service slug already exists") from exc

    return service


async def list_services(*, session: AsyncSession, account_id: int) -> list[Service]:
    statement = (
        select(Service)
        .options(
            selectinload(Service.tags),
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.pricing),
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.upstream),
        )
        .execution_options(populate_existing=True)
        .where(Service.provider_account_id == account_id)
        .order_by(desc(Service.created_at), desc(Service.id))
    )
    result = await session.scalars(statement)
    return list(result.all())


async def get_service(*, session: AsyncSession, account_id: int, service_id: int) -> Service:
    return await service_access.load_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )


async def update_service(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
    updates: dict[str, str | None],
) -> Service:
    if not updates:
        raise InvalidInputError("at least one field must be provided")

    unknown_fields = set(updates) - SERVICE_UPDATE_FIELDS
    if unknown_fields:
        raise InvalidInputError(f"unknown update fields: {', '.join(sorted(unknown_fields))}")

    now = datetime.now(UTC)
    service = await service_access.load_owned_service_for_update(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    update_fields: dict[str, str | None] = {}
    try:
        if "name" in updates:
            name = updates["name"]
            if name is None:
                raise InvalidInputError("name cannot be null")
            update_fields["name"] = normalize_service_name(name)
        if "summary" in updates:
            summary = updates["summary"]
            if summary is None:
                raise InvalidInputError("summary cannot be null")
            update_fields["summary"] = normalize_service_summary(summary)
        if "description" in updates:
            update_fields["description"] = normalize_service_description(updates["description"])
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    impact = RevisionService.classify_service_update(update_fields)
    _ensure_service_update_allowed(service, impact=impact)

    for attribute_name, value in update_fields.items():
        setattr(service, attribute_name, value)
    service.updated_at = now

    if service.lifecycle is ServiceLifecycle.ACTIVE:
        await RevisionService(session).create_revision_if_material_service_update(
            service,
            update_fields=update_fields,
        )

    await session.commit()
    return service


async def replace_tags(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
    tags: list[str],
) -> Service:
    if len(tags) > SERVICE_TAGS_MAX_COUNT:
        raise InvalidInputError(f"at most {SERVICE_TAGS_MAX_COUNT} tags are allowed")

    now = datetime.now(UTC)
    service = await service_access.load_owned_service_for_update(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not mutable outside draft")

    try:
        normalized_tags = {normalize_tag(tag) for tag in tags}
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc

    existing_tags = {service_tag.tag for service_tag in service.tags}
    for service_tag in list(service.tags):
        if service_tag.tag not in normalized_tags:
            service.tags.remove(service_tag)
    for tag in sorted(normalized_tags - existing_tags):
        service.tags.append(ServiceTag(service_id=service.id, tag=tag))
    service.updated_at = now
    await session.commit()
    return service


def _ensure_service_update_allowed(service: Service, *, impact: UpdateImpact) -> None:
    if service.lifecycle is ServiceLifecycle.DRAFT:
        return
    if service.lifecycle is ServiceLifecycle.ACTIVE and impact is UpdateImpact.NON_MATERIAL:
        return
    raise InvalidStateError("service is not mutable outside draft")
