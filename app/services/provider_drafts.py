import re
from datetime import UTC, datetime

from sqlalchemy import Select, delete, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.db.models.service_tag import ServiceTag
from app.services.revision_service import RevisionService, UpdateImpact

TAG_TOKEN_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


async def create_service(
    *,
    session: AsyncSession,
    account_id: int,
    slug: str,
    name: str,
    summary: str,
    description: str | None,
) -> Service:
    normalized_slug = _normalize_slug(slug)
    normalized_name = _normalize_service_name(name)
    normalized_summary = _normalize_summary(summary)
    normalized_description = _normalize_description(description)

    service = Service(
        provider_account_id=account_id,
        slug=normalized_slug,
        name=normalized_name,
        summary=normalized_summary,
        description=normalized_description,
    )
    session.add(service)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("service slug already exists") from exc

    return await _require_owned_service(
        session=session,
        account_id=account_id,
        service_id=service.id,
    )


async def list_services(*, session: AsyncSession, account_id: int) -> list[Service]:
    statement = (
        _service_with_relations()
        .where(Service.provider_account_id == account_id)
        .order_by(desc(Service.created_at), desc(Service.id))
    )
    result = await session.scalars(statement)
    return list(result.all())


async def get_service(*, session: AsyncSession, account_id: int, service_id: int) -> Service:
    return await _require_owned_service(
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

    allowed_fields = {"name", "summary", "description"}
    unknown_fields = set(updates) - allowed_fields
    if unknown_fields:
        unknown_field = sorted(unknown_fields)[0]
        raise InvalidInputError(f"unknown update field: {unknown_field}")

    now = datetime.now(UTC)
    service = await _require_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    update_fields: dict[str, str | None] = {}
    if "name" in updates:
        name = updates["name"]
        if name is None:
            raise InvalidInputError("name cannot be null")
        update_fields["name"] = _normalize_service_name(name)
    if "summary" in updates:
        summary = updates["summary"]
        if summary is None:
            raise InvalidInputError("summary cannot be null")
        update_fields["summary"] = _normalize_summary(summary)
    if "description" in updates:
        update_fields["description"] = _normalize_description(updates["description"])
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
    return await _require_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )


async def replace_tags(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
    tags: list[str],
) -> Service:
    now = datetime.now(UTC)
    locked_service_id = await session.scalar(
        select(Service.id)
        .where(
            Service.id == service_id,
            Service.provider_account_id == account_id,
        )
        .with_for_update(),
    )
    if locked_service_id is None:
        raise NotFoundError("service not found")
    service = await _require_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )
    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not mutable outside draft")

    normalized_tags = sorted({_normalize_tag(tag) for tag in tags})

    await session.execute(delete(ServiceTag).where(ServiceTag.service_id == service.id))
    session.add_all(
        [ServiceTag(service_id=service.id, tag=tag) for tag in normalized_tags],
    )
    service.updated_at = now
    await session.commit()
    return await _require_owned_service(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )


def _service_with_relations() -> Select[tuple[Service]]:
    return (
        select(Service)
        .options(
            selectinload(Service.tags),
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.pricing),
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.upstream),
        )
        .execution_options(populate_existing=True)
    )


async def _require_owned_service(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
) -> Service:
    statement = _service_with_relations().where(
        Service.id == service_id,
        Service.provider_account_id == account_id,
    )
    service = await session.scalar(statement)
    if service is None:
        raise NotFoundError("service not found")
    return service


def _ensure_service_update_allowed(service: Service, *, impact: UpdateImpact) -> None:
    if service.lifecycle is ServiceLifecycle.DRAFT:
        return
    if service.lifecycle is ServiceLifecycle.ACTIVE and impact is UpdateImpact.NON_MATERIAL:
        return
    raise InvalidStateError("service is not mutable outside draft")


def _normalize_slug(slug: str) -> str:
    normalized_slug = slug.strip()
    if TAG_TOKEN_PATTERN.fullmatch(normalized_slug) is None:
        raise InvalidInputError("slug must be a lowercase slug token")
    if normalized_slug.isdigit():
        raise InvalidInputError("slug must include at least one lowercase letter")
    if len(normalized_slug) > 255:
        raise InvalidInputError("slug must be at most 255 characters")
    return normalized_slug


def _normalize_service_name(name: str) -> str:
    normalized_name = name.strip()
    if not normalized_name:
        raise InvalidInputError("name must not be blank")
    if len(normalized_name) > 255:
        raise InvalidInputError("name must be at most 255 characters")
    return normalized_name


def _normalize_summary(summary: str) -> str:
    normalized_summary = summary.strip()
    if not normalized_summary:
        raise InvalidInputError("summary must not be blank")
    if len(normalized_summary) > 500:
        raise InvalidInputError("summary must be at most 500 characters")
    return normalized_summary


def _normalize_description(description: str | None) -> str | None:
    if description is None:
        return None
    normalized_description = description.strip()
    if not normalized_description:
        raise InvalidInputError("description must not be blank")
    if len(normalized_description) > 5000:
        raise InvalidInputError("description must be at most 5000 characters")
    return normalized_description


def _normalize_tag(tag: str) -> str:
    normalized_tag = tag.strip().lower()
    if TAG_TOKEN_PATTERN.fullmatch(normalized_tag) is None:
        raise InvalidInputError("tags must be lowercase slug tokens")
    return normalized_tag
