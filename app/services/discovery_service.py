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
        visible_services: list[Service] = []
        for service in services:
            if not self._has_public_endpoints(service):
                continue
            try:
                await self._moderation_service.ensure_service_listed(service.id)
            except ServiceUnavailableError:
                continue
            visible_services.append(service)
        return visible_services

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
