from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.enums import ServiceLifecycle
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


class ProviderEndpointService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._provider_profile_repo = ProviderProfileRepository(session)
        self._service_repo = ServiceRepository(session)
        self._endpoint_repo = ServiceEndpointRepository(session)
        self._upstream_repo = ProviderUpstreamRepository()

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
        if not request.model_fields_set:
            raise ProviderServiceValidationError("at least one field must be provided")

        endpoint = await self.get_endpoint(actor, endpoint_id=endpoint_id)
        self._ensure_draft(endpoint.service)
        self._endpoint_repo.update_endpoint(
            endpoint,
            name=request.name if "name" in request.model_fields_set else None,
            summary=request.summary if "summary" in request.model_fields_set else None,
            description=(
                request.description if "description" in request.model_fields_set else None
            ),
            access_mode=(
                request.access_mode if "access_mode" in request.model_fields_set else None
            ),
            request_schema=(
                request.request_schema if "request_schema" in request.model_fields_set else None
            ),
            response_schema=(
                request.response_schema if "response_schema" in request.model_fields_set else None
            ),
            timeout_seconds=(
                request.timeout_seconds if "timeout_seconds" in request.model_fields_set else None
            ),
            is_enabled=(request.is_enabled if "is_enabled" in request.model_fields_set else None),
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
