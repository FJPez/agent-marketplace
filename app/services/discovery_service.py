from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.service import Service
from app.repositories.service_repo import ServiceRepository


class DiscoveryNotFoundError(Exception):
    pass


class DiscoveryService:
    def __init__(self, session: AsyncSession) -> None:
        self._service_repo = ServiceRepository(session)

    async def list_services(self) -> list[Service]:
        services = await self._service_repo.list_public()
        return [service for service in services if self._has_public_endpoints(service)]

    async def get_service(self, *, service_id_or_slug: str) -> Service:
        service = await self._service_repo.get_public(
            service_id_or_slug=service_id_or_slug,
        )
        if service is None or not self._has_public_endpoints(service):
            raise DiscoveryNotFoundError("service not found")
        return service

    def _has_public_endpoints(self, service: Service) -> bool:
        return any(endpoint.is_enabled for endpoint in service.endpoints)
