"""Public catalogue reads for the discovery API."""

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ServiceLifecycle
from app.core.errors import NotFoundError
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.discovery import PublicServiceRef
from app.services.moderation_service import ModerationService, ServiceUnavailableError


async def list_services(*, session: AsyncSession) -> list[Service]:
    """Return active, listed services that expose at least one enabled endpoint."""
    statement = (
        select(Service)
        .options(selectinload(Service.tags))
        .where(
            Service.lifecycle == ServiceLifecycle.ACTIVE,
            Service.endpoints.any(ServiceEndpoint.is_enabled.is_(True)),
        )
        .order_by(desc(Service.created_at), desc(Service.id))
    )
    result = await session.scalars(statement)
    visible_services = list(result.all())
    if not visible_services:
        return []

    unlisted_ids = await ModerationService(session).get_unlisted_service_ids(
        [service.id for service in visible_services],
    )
    return [service for service in visible_services if service.id not in unlisted_ids]


async def get_service(*, session: AsyncSession, service_ref: PublicServiceRef) -> Service:
    """Return the single active, listed service addressed by an id or a slug."""
    statement = (
        select(Service)
        .options(
            selectinload(Service.tags),
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.price),
        )
        .where(Service.lifecycle == ServiceLifecycle.ACTIVE)
    )
    statement = statement.where(
        Service.id == service_ref.id
        if service_ref.id is not None
        else Service.slug == service_ref.slug
    )

    service = await session.scalar(statement)
    if service is None or not any(endpoint.is_enabled for endpoint in service.endpoints):
        raise NotFoundError("service not found")

    try:
        await ModerationService(session).ensure_service_available(service.id)
    except ServiceUnavailableError as exc:
        # Suspended and delisted services must stay indistinguishable from missing ones publicly.
        raise NotFoundError("service not found") from exc
    return service
