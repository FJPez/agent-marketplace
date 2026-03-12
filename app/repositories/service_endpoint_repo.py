from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.enums import AccessMode
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.repositories._sentinel import UNSET, UnsetType


class ServiceEndpointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        service_id: int,
        key: str,
        name: str,
        summary: str | None,
        description: str | None,
        access_mode: AccessMode,
        request_schema: dict[str, object],
        response_schema: dict[str, object],
        timeout_seconds: int,
        is_enabled: bool,
    ) -> ServiceEndpoint:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key=key,
            name=name,
            summary=summary,
            description=description,
            access_mode=access_mode,
            request_schema=request_schema,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds,
            is_enabled=is_enabled,
        )
        self._session.add(endpoint)
        return endpoint

    async def get_owned(
        self,
        *,
        endpoint_id: int,
        provider_account_id: int,
    ) -> ServiceEndpoint | None:
        statement = (
            select(ServiceEndpoint)
            .join(Service)
            .options(
                joinedload(ServiceEndpoint.service),
                selectinload(ServiceEndpoint.upstream),
            )
            .execution_options(populate_existing=True)
            .where(
                ServiceEndpoint.id == endpoint_id,
                Service.provider_account_id == provider_account_id,
            )
        )
        return await self._session.scalar(statement)

    def update_endpoint(
        self,
        endpoint: ServiceEndpoint,
        *,
        name: str | UnsetType = UNSET,
        summary: str | None | UnsetType = UNSET,
        description: str | None | UnsetType = UNSET,
        access_mode: AccessMode | UnsetType = UNSET,
        request_schema: dict[str, object] | UnsetType = UNSET,
        response_schema: dict[str, object] | UnsetType = UNSET,
        timeout_seconds: int | UnsetType = UNSET,
        is_enabled: bool | UnsetType = UNSET,
    ) -> ServiceEndpoint:
        for attribute_name, value in (
            ("name", name),
            ("summary", summary),
            ("description", description),
            ("access_mode", access_mode),
            ("request_schema", request_schema),
            ("response_schema", response_schema),
            ("timeout_seconds", timeout_seconds),
            ("is_enabled", is_enabled),
        ):
            if value is not UNSET:
                setattr(endpoint, attribute_name, value)
        endpoint.updated_at = datetime.now(UTC)
        return endpoint
