from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.service import Service
from app.repositories.service_repo import ServiceRepository
from app.services.moderation_service import ModerationService, ServiceUnavailableError


class DiscoveryNotFoundError(Exception):
    pass


class DiscoveryService:
    def __init__(self, session: AsyncSession) -> None:
        self._service_repo = ServiceRepository(session)
        self._moderation_service = ModerationService(session)

    async def list_services(self) -> list[Service]:
        services = await self._service_repo.list_public()
        visible_services = [s for s in services if self._has_public_endpoints(s)]
        if not visible_services:
            return []
        service_ids = [s.id for s in visible_services]
        unlisted_ids = await self._moderation_service.get_unlisted_service_ids(service_ids)
        return [s for s in visible_services if s.id not in unlisted_ids]

    async def get_service(self, *, service_id_or_slug: str) -> Service:
        service = await self._service_repo.get_public(
            service_id_or_slug=service_id_or_slug,
        )
        if service is None or not self._has_public_endpoints(service):
            raise DiscoveryNotFoundError("service not found")
        try:
            await self._moderation_service.ensure_service_listed(service.id)
        except ServiceUnavailableError as exc:
            raise DiscoveryNotFoundError("service not found") from exc
        return service

    def _has_public_endpoints(self, service: Service) -> bool:
        return any(endpoint.is_enabled for endpoint in service.endpoints)
