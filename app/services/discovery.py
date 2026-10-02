"""Public catalogue reads for the discovery API."""

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ServiceLifecycle
from app.core.errors import NotFoundError
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.service_ref import PublicServiceRef
from app.services import moderation


async def list_services(*, session: AsyncSession) -> list[Service]:
    """Return active, listed services that expose at least one enabled endpoint."""
    statement = (
        select(Service)
        .options(selectinload(Service.tags))
        .where(
            Service.lifecycle == ServiceLifecycle.ACTIVE,
            moderation.is_clear(),
            Service.endpoints.any(ServiceEndpoint.is_enabled.is_(True)),
        )
        .order_by(desc(Service.created_at), desc(Service.id))
    )
    result = await session.scalars(statement)
    return list(result.all())


async def get_service(*, session: AsyncSession, service_ref: PublicServiceRef) -> Service:
    """Return the single active, listed service addressed by an id or a slug."""
    statement = (
        select(Service)
        .options(
            selectinload(Service.tags),
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.current_price),
        )
        # Suspended and delisted services stay indistinguishable from missing ones.
        .where(Service.lifecycle == ServiceLifecycle.ACTIVE, moderation.is_clear())
    )
    if isinstance(service_ref, int):
        statement = statement.where(Service.id == service_ref)
    else:
        statement = statement.where(Service.slug == service_ref)

    service = await session.scalar(statement)
    if service is None or not any(endpoint.is_enabled for endpoint in service.endpoints):
        raise NotFoundError("service not found")
    return service
