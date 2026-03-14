from typing import TypedDict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.enums import AccessMode, ServiceLifecycle
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.repositories.provider_profile_repo import ProviderProfileRepository
from app.repositories.provider_upstream_repo import ProviderUpstreamRepository
from app.repositories.service_endpoint_repo import ServiceEndpointRepository
from app.repositories.service_repo import ServiceRepository
from app.schemas.service import (
    EndpointCreateRequest,
    EndpointUpdateRequest,
    EndpointUpstreamRequest,
)
from app.services.provider_service_errors import (
    ProviderServiceConflictError,
    ProviderServiceNotFoundError,
    ProviderServiceStateError,
    ProviderServiceValidationError,
)
from app.services.revision_service import RevisionService


class _EndpointUpdateFields(TypedDict, total=False):
    name: str
    summary: str | None
    description: str | None
    access_mode: AccessMode
    request_schema: dict[str, object]
    response_schema: dict[str, object]
    timeout_seconds: int
    is_enabled: bool


class ProviderEndpointService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._provider_profile_repo = ProviderProfileRepository(session)
        self._service_repo = ServiceRepository(session)
        self._endpoint_repo = ServiceEndpointRepository(session)
        self._upstream_repo = ProviderUpstreamRepository()
        self._revision_service = RevisionService(session)

    async def create_endpoint(
        self,
        actor: ActorContext,
        *,
        service_id: int,
        request: EndpointCreateRequest,
    ) -> ServiceEndpoint:
        service = await self._get_owned_service(actor.account_id, service_id=service_id)
        self._ensure_draft(service)
        endpoint = self._endpoint_repo.add(
            service_id=service.id,
            key=request.key,
            name=request.name,
            summary=request.summary,
            description=request.description,
            access_mode=request.access_mode,
            request_schema=request.request_schema,
            response_schema=request.response_schema,
            timeout_seconds=request.timeout_seconds,
            is_enabled=request.is_enabled,
        )

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ProviderServiceConflictError(
                "endpoint key already exists for this service",
            ) from exc

        return await self.get_endpoint(actor, endpoint_id=endpoint.id)

    async def get_endpoint(
        self,
        actor: ActorContext,
        *,
        endpoint_id: int,
    ) -> ServiceEndpoint:
        await self._require_provider_profile(actor.account_id)
        endpoint = await self._endpoint_repo.get_owned(
            endpoint_id=endpoint_id,
            provider_account_id=actor.account_id,
        )
        if endpoint is None:
            raise ProviderServiceNotFoundError("endpoint not found")
        return endpoint

    async def update_endpoint(
        self,
        actor: ActorContext,
        *,
        endpoint_id: int,
        request: EndpointUpdateRequest,
    ) -> ServiceEndpoint:
        raw_update_fields = request.model_dump(exclude_unset=True)
        if not raw_update_fields:
            raise ProviderServiceValidationError("at least one field must be provided")

        endpoint = await self.get_endpoint(actor, endpoint_id=endpoint_id)
        update_fields: _EndpointUpdateFields = {}
        if "name" in raw_update_fields:
            if raw_update_fields["name"] is None:
                raise ProviderServiceValidationError("name cannot be null")
            update_fields["name"] = raw_update_fields["name"]
        if "summary" in raw_update_fields:
            update_fields["summary"] = raw_update_fields["summary"]
        if "description" in raw_update_fields:
            update_fields["description"] = raw_update_fields["description"]
        if "access_mode" in raw_update_fields:
            if raw_update_fields["access_mode"] is None:
                raise ProviderServiceValidationError("access_mode cannot be null")
            update_fields["access_mode"] = raw_update_fields["access_mode"]
        if "request_schema" in raw_update_fields:
            if raw_update_fields["request_schema"] is None:
                raise ProviderServiceValidationError("request_schema cannot be null")
            update_fields["request_schema"] = raw_update_fields["request_schema"]
        if "response_schema" in raw_update_fields:
            if raw_update_fields["response_schema"] is None:
                raise ProviderServiceValidationError("response_schema cannot be null")
            update_fields["response_schema"] = raw_update_fields["response_schema"]
        if "timeout_seconds" in raw_update_fields:
            if raw_update_fields["timeout_seconds"] is None:
                raise ProviderServiceValidationError("timeout_seconds cannot be null")
            update_fields["timeout_seconds"] = raw_update_fields["timeout_seconds"]
        if "is_enabled" in raw_update_fields:
            if raw_update_fields["is_enabled"] is None:
                raise ProviderServiceValidationError("is_enabled cannot be null")
            update_fields["is_enabled"] = raw_update_fields["is_enabled"]
        self._ensure_endpoint_update_allowed(endpoint.service)
        self._endpoint_repo.update_endpoint(
            endpoint,
            **update_fields,
        )
        if endpoint.service.lifecycle is ServiceLifecycle.ACTIVE:
            service = await self._get_owned_service(
                actor.account_id,
                service_id=endpoint.service_id,
            )
            await self._revision_service.create_revision_if_material_endpoint_update(
                service,
                update_fields=update_fields,
            )
        await self._session.commit()
        return await self.get_endpoint(actor, endpoint_id=endpoint.id)

    async def upsert_upstream(
        self,
        actor: ActorContext,
        *,
        endpoint_id: int,
        request: EndpointUpstreamRequest,
    ) -> None:
        endpoint = await self.get_endpoint(actor, endpoint_id=endpoint_id)
        self._ensure_draft(endpoint.service)
        await self._upstream_repo.upsert(
            endpoint,
            base_url=str(request.base_url),
            path=request.path,
            http_method=request.http_method,
            config=request.config,
        )
        await self._session.commit()

    async def _get_owned_service(
        self,
        provider_account_id: int,
        *,
        service_id: int,
    ) -> Service:
        await self._require_provider_profile(provider_account_id)
        service = await self._service_repo.get_owned(
            service_id=service_id,
            provider_account_id=provider_account_id,
        )
        if service is None:
            raise ProviderServiceNotFoundError("service not found")
        return service

    async def _require_provider_profile(self, account_id: int) -> None:
        profile = await self._provider_profile_repo.get_by_account_id(account_id)
        if profile is None:
            raise ProviderServiceNotFoundError("provider profile not found")

    def _ensure_draft(self, service: Service) -> None:
        if service.lifecycle is not ServiceLifecycle.DRAFT:
            raise ProviderServiceStateError("service is not mutable outside draft")

    def _ensure_endpoint_update_allowed(self, service: Service) -> None:
        if service.lifecycle in {ServiceLifecycle.DRAFT, ServiceLifecycle.ACTIVE}:
            return
        raise ProviderServiceStateError("service is not mutable outside draft")
