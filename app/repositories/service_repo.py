from datetime import UTC, datetime
from typing import TypedDict, Unpack

from sqlalchemy import Select, delete, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ServiceLifecycle
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.db.models.service_tag import ServiceTag


class _ServiceUpdateFields(TypedDict, total=False):
    name: str
    summary: str
    description: str | None


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

    def add(
        self,
        *,
        provider_account_id: int,
        slug: str,
        name: str,
        summary: str,
        description: str | None,
    ) -> Service:
        service = Service(
            provider_account_id=provider_account_id,
            slug=slug,
            name=name,
            summary=summary,
            description=description,
        )
        self._session.add(service)
        return service

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

    async def list_by_provider_account_id(
        self,
        *,
        provider_account_id: int,
    ) -> list[Service]:
        statement = (
            _service_with_relations()
            .where(Service.provider_account_id == provider_account_id)
            .order_by(desc(Service.created_at), desc(Service.id))
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def list_public(self) -> list[Service]:
        statement = (
            _service_with_relations()
            .where(Service.lifecycle == ServiceLifecycle.ACTIVE)
            .order_by(desc(Service.created_at), desc(Service.id))
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def get_public(self, *, service_id_or_slug: str) -> Service | None:
        statement = _service_with_relations().where(
            Service.lifecycle == ServiceLifecycle.ACTIVE,
        )
        if service_id_or_slug.isdigit():
            statement = statement.where(
                or_(
                    Service.id == int(service_id_or_slug),
                    Service.slug == service_id_or_slug,
                ),
            )
        else:
            statement = statement.where(Service.slug == service_id_or_slug)
        return await self._session.scalar(statement)

    def update_service(
        self,
        service: Service,
        **updates: Unpack[_ServiceUpdateFields],
    ) -> Service:
        for attribute_name, value in updates.items():
            setattr(service, attribute_name, value)
        service.updated_at = datetime.now(UTC)
        return service

    def set_lifecycle(self, service: Service, *, lifecycle: ServiceLifecycle) -> Service:
        service.lifecycle = lifecycle
        service.updated_at = datetime.now(UTC)
        return service

    async def replace_tags(self, service: Service, *, tags: list[str]) -> Service:
        await self._session.execute(
            delete(ServiceTag).where(ServiceTag.service_id == service.id),
        )
        self._session.add_all(
            [
                ServiceTag(
                    service_id=service.id,
                    tag=tag,
                )
                for tag in tags
            ],
        )
        service.updated_at = datetime.now(UTC)
        return service
