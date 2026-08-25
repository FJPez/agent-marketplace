from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ServiceLifecycle
from app.core.errors import ConflictError, InvalidStateError
from app.db.errors import is_unique_violation
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.db.models.service_tag import ServiceTag
from app.schemas.service import (
    ServiceCreateRequest,
    ServiceTagsUpdateRequest,
    ServiceUpdateRequest,
)
from app.services import service_access


async def create_service(
    *,
    session: AsyncSession,
    account_id: int,
    request: ServiceCreateRequest,
) -> Service:
    service = Service(
        provider_account_id=account_id,
        slug=request.slug,
        name=request.name,
        summary=request.summary,
        description=request.description,
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
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.price),
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
    changes: ServiceUpdateRequest,
) -> Service:
    service = await service_access.load_owned_service_for_update(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )

    # Only descriptive columns are patchable and none live in another table, so a
    # single effective-changes mapping is enough here (contrast update_endpoint,
    # where pricing is tracked separately).
    supplied = changes.model_dump(exclude_unset=True)
    effective_changes = {
        name: value for name, value in supplied.items() if value != getattr(service, name)
    }
    if not effective_changes:
        return service

    _ensure_service_update_allowed(service)

    # Stamped after the lock wait so the timestamp reflects when the row was
    # actually mutated.
    now = datetime.now(UTC)

    for attribute_name, value in effective_changes.items():
        setattr(service, attribute_name, value)
    service.updated_at = now
    await session.commit()
    return service


async def replace_tags(
    *,
    session: AsyncSession,
    account_id: int,
    service_id: int,
    request: ServiceTagsUpdateRequest,
) -> Service:
    service = await service_access.load_owned_service_for_update(
        session=session,
        account_id=account_id,
        service_id=service_id,
    )

    normalized_tags = set(request.tags)
    existing_tags = {service_tag.tag for service_tag in service.tags}
    if normalized_tags == existing_tags:
        return service

    if service.lifecycle is not ServiceLifecycle.DRAFT:
        raise InvalidStateError("service is not mutable outside draft")

    now = datetime.now(UTC)
    for service_tag in list(service.tags):
        if service_tag.tag not in normalized_tags:
            service.tags.remove(service_tag)
    for tag in sorted(normalized_tags - existing_tags):
        service.tags.append(ServiceTag(service_id=service.id, tag=tag))
    service.updated_at = now
    await session.commit()
    return service


def _ensure_service_update_allowed(service: Service) -> None:
    if service.lifecycle in {ServiceLifecycle.DRAFT, ServiceLifecycle.ACTIVE}:
        return
    raise InvalidStateError("service is not mutable outside draft")
