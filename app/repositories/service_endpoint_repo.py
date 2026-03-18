from datetime import UTC, datetime
from typing import TypedDict, Unpack

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.enums import AccessMode
from app.core.json_types import JsonObject
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint


class _EndpointUpdateFields(TypedDict, total=False):
    name: str
    summary: str | None
    description: str | None
    access_mode: AccessMode
    request_schema: JsonObject
    response_schema: JsonObject
    timeout_seconds: int
    is_enabled: bool


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
        request_schema: JsonObject,
        response_schema: JsonObject,
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
                selectinload(ServiceEndpoint.pricing),
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
        **updates: Unpack[_EndpointUpdateFields],
    ) -> ServiceEndpoint:
        for attribute_name, value in updates.items():
            setattr(endpoint, attribute_name, value)
        endpoint.updated_at = datetime.now(UTC)
        return endpoint
