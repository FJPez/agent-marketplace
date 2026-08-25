from datetime import UTC, datetime

from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ServiceLifecycle
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint


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


class ServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned(
        self,
        *,
        service_id: int,
        provider_account_id: int,
    ) -> Service | None:
        statement = _service_with_relations().where(
            Service.id == service_id,
            Service.provider_account_id == provider_account_id,
        )
        return await self._session.scalar(statement)

    async def list_public(self) -> list[Service]:
        statement = (
            _service_with_relations()
            .where(Service.lifecycle == ServiceLifecycle.ACTIVE)
            .order_by(desc(Service.created_at), desc(Service.id))
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def get_by_id(self, *, service_id: int) -> Service | None:
        statement = _service_with_relations().where(Service.id == service_id)
        return await self._session.scalar(statement)

    async def get_by_id_for_update(self, *, service_id: int) -> Service | None:
        locked_service_id = await self._session.scalar(
            select(Service.id).where(Service.id == service_id).with_for_update(),
        )
        if locked_service_id is None:
            return None
        return await self.get_by_id(service_id=service_id)

    async def get_public(self, *, service_id_or_slug: str) -> Service | None:
        statement = _service_with_relations().where(
            Service.lifecycle == ServiceLifecycle.ACTIVE,
        )
        if service_id_or_slug.isdigit():
            statement = statement.where(Service.id == int(service_id_or_slug))
        else:
            statement = statement.where(Service.slug == service_id_or_slug)
        return await self._session.scalar(statement)

    async def get_owned_for_update(
        self,
        *,
        service_id: int,
        provider_account_id: int,
    ) -> Service | None:
        locked_service_id = await self._session.scalar(
            select(Service.id)
            .where(
                Service.id == service_id,
                Service.provider_account_id == provider_account_id,
            )
            .with_for_update(),
        )
        if locked_service_id is None:
            return None
        return await self.get_owned(
            service_id=service_id,
            provider_account_id=provider_account_id,
        )

    def set_lifecycle(self, service: Service, *, lifecycle: ServiceLifecycle) -> Service:
        service.lifecycle = lifecycle
        service.updated_at = datetime.now(UTC)
        return service
