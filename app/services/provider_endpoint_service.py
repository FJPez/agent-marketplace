from typing import TypedDict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.config import get_settings
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.core.upstream_targets import UnsafeUpstreamTargetError, validate_upstream_base_url
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.repositories.pricing_model_repo import PricingModelRepository
from app.repositories.provider_upstream_repo import ProviderUpstreamRepository
from app.repositories.service_endpoint_repo import ServiceEndpointRepository
from app.repositories.service_repo import ServiceRepository
from app.schemas.service import (
    EndpointCreateRequest,
    EndpointPricingRequest,
    EndpointUpdateRequest,
    EndpointUpstreamRequest,
)
from app.services.moderation_service import ModerationService, ServiceUnavailableError
from app.services.provider_service_errors import (
    ProviderServiceConflictError,
    ProviderServiceNotFoundError,
    ProviderServiceStateError,
    ProviderServiceValidationError,
)
from app.services.revision_service import RevisionService, UpdateImpact


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
        self._service_repo = ServiceRepository(session)
        self._endpoint_repo = ServiceEndpointRepository(session)
        self._pricing_repo = PricingModelRepository(session)
        self._upstream_repo = ProviderUpstreamRepository()
        self._moderation_service = ModerationService(session)
        self._revision_service = RevisionService(session)

    async def create_endpoint(
        self,
        actor: ActorContext,
        *,
        service_id: int,
        request: EndpointCreateRequest,
    ) -> ServiceEndpoint:
        service = await self._get_owned_service_for_update(
            actor.account_id,
            service_id=service_id,
        )
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
        await self._session.flush()
        await self._sync_pricing(
            endpoint,
            pricing=request.pricing,
            access_mode=request.access_mode,
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

        service, endpoint = await self._get_owned_service_and_endpoint_for_update(
            actor.account_id,
            endpoint_id=endpoint_id,
        )
        update_fields: _EndpointUpdateFields = {}
        revision_fields: dict[str, object] = {}
        if "name" in raw_update_fields:
            if raw_update_fields["name"] is None:
                raise ProviderServiceValidationError("name cannot be null")
            update_fields["name"] = raw_update_fields["name"]
            revision_fields["name"] = raw_update_fields["name"]
        if "summary" in raw_update_fields:
            update_fields["summary"] = raw_update_fields["summary"]
            revision_fields["summary"] = raw_update_fields["summary"]
        if "description" in raw_update_fields:
            update_fields["description"] = raw_update_fields["description"]
            revision_fields["description"] = raw_update_fields["description"]
        if "access_mode" in raw_update_fields:
            if raw_update_fields["access_mode"] is None:
                raise ProviderServiceValidationError("access_mode cannot be null")
            update_fields["access_mode"] = raw_update_fields["access_mode"]
            revision_fields["access_mode"] = raw_update_fields["access_mode"]
        if "request_schema" in raw_update_fields:
            if raw_update_fields["request_schema"] is None:
                raise ProviderServiceValidationError("request_schema cannot be null")
            update_fields["request_schema"] = raw_update_fields["request_schema"]
            revision_fields["request_schema"] = raw_update_fields["request_schema"]
        if "response_schema" in raw_update_fields:
            if raw_update_fields["response_schema"] is None:
                raise ProviderServiceValidationError("response_schema cannot be null")
            update_fields["response_schema"] = raw_update_fields["response_schema"]
            revision_fields["response_schema"] = raw_update_fields["response_schema"]
        if "timeout_seconds" in raw_update_fields:
            if raw_update_fields["timeout_seconds"] is None:
                raise ProviderServiceValidationError("timeout_seconds cannot be null")
            update_fields["timeout_seconds"] = raw_update_fields["timeout_seconds"]
            revision_fields["timeout_seconds"] = raw_update_fields["timeout_seconds"]
        if "is_enabled" in raw_update_fields:
            if raw_update_fields["is_enabled"] is None:
                raise ProviderServiceValidationError("is_enabled cannot be null")
            update_fields["is_enabled"] = raw_update_fields["is_enabled"]
            revision_fields["is_enabled"] = raw_update_fields["is_enabled"]
        if "pricing" in raw_update_fields:
            revision_fields["pricing"] = raw_update_fields["pricing"]
        await self._ensure_endpoint_update_allowed(
            service,
            update_fields=revision_fields,
        )
        self._endpoint_repo.update_endpoint(
            endpoint,
            **update_fields,
        )
        await self._sync_pricing(
            endpoint,
            pricing=request.pricing,
            access_mode=endpoint.access_mode,
        )
        if service.lifecycle is ServiceLifecycle.ACTIVE:
            self._ensure_active_endpoint_pricing_valid(endpoint)
            await self._revision_service.create_revision_if_material_endpoint_update(
                service,
                update_fields=revision_fields,
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
        service, endpoint = await self._get_owned_service_and_endpoint_for_update(
            actor.account_id,
            endpoint_id=endpoint_id,
        )
        self._ensure_draft(service)
        try:
            validated_base_url = validate_upstream_base_url(
                str(request.base_url),
                settings=get_settings(),
            )
        except UnsafeUpstreamTargetError as exc:
            raise ProviderServiceValidationError(str(exc)) from exc
        await self._upstream_repo.upsert(
            endpoint,
            base_url=validated_base_url,
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
        service = await self._service_repo.get_owned(
            service_id=service_id,
            provider_account_id=provider_account_id,
        )
        if service is None:
            raise ProviderServiceNotFoundError("service not found")
        return service

    async def _get_owned_service_for_update(
        self,
        provider_account_id: int,
        *,
        service_id: int,
    ) -> Service:
        service = await self._service_repo.get_owned_for_update(
            service_id=service_id,
            provider_account_id=provider_account_id,
        )
        if service is None:
            raise ProviderServiceNotFoundError("service not found")
        return service

    async def _get_owned_service_and_endpoint_for_update(
        self,
        provider_account_id: int,
        *,
        endpoint_id: int,
    ) -> tuple[Service, ServiceEndpoint]:
        service = await self._service_repo.get_owned_by_endpoint_for_update(
            endpoint_id=endpoint_id,
            provider_account_id=provider_account_id,
        )
        if service is None:
            raise ProviderServiceNotFoundError("endpoint not found")
        endpoint = next(
            (candidate for candidate in service.endpoints if candidate.id == endpoint_id),
            None,
        )
        if endpoint is None:
            raise ProviderServiceNotFoundError("endpoint not found")
        return service, endpoint

    def _ensure_draft(self, service: Service) -> None:
        if service.lifecycle is not ServiceLifecycle.DRAFT:
            raise ProviderServiceStateError("service is not mutable outside draft")

    async def _ensure_endpoint_update_allowed(
        self,
        service: Service,
        *,
        update_fields: dict[str, object],
    ) -> None:
        if service.lifecycle is ServiceLifecycle.DRAFT:
            return
        if service.lifecycle is ServiceLifecycle.ACTIVE:
            if (
                self._revision_service.classify_endpoint_update(update_fields)
                is not UpdateImpact.MATERIAL
            ):
                return
            try:
                await self._moderation_service.ensure_service_publishable(service.id)
            except ServiceUnavailableError as exc:
                raise ProviderServiceStateError(f"service is {exc.state.value}") from exc
            return
        raise ProviderServiceStateError("service is not mutable outside draft")

    def _ensure_active_endpoint_pricing_valid(self, endpoint: ServiceEndpoint) -> None:
        if endpoint.access_mode is not AccessMode.PAID:
            return
        pricing = endpoint.pricing
        if (
            pricing is None
            or pricing.pricing_type is not PricingModelType.FIXED_PER_CALL
            or pricing.amount_minor is None
            or pricing.currency is None
        ):
            raise ProviderServiceValidationError(
                "active paid endpoints must define fixed_per_call pricing",
            )

    async def _sync_pricing(
        self,
        endpoint: ServiceEndpoint,
        *,
        pricing: EndpointPricingRequest | None,
        access_mode: AccessMode,
    ) -> None:
        if access_mode is AccessMode.FREE:
            if pricing is not None and pricing.pricing_type is not PricingModelType.FREE:
                raise ProviderServiceValidationError(
                    "free endpoints must use free pricing",
                )
            self._pricing_repo.upsert_free(endpoint)
            return

        if pricing is None:
            if (
                endpoint.pricing is not None
                and endpoint.pricing.pricing_type is PricingModelType.FREE
            ):
                await self._pricing_repo.delete_for_endpoint(endpoint)
            return

        if pricing.pricing_type is not PricingModelType.FIXED_PER_CALL:
            raise ProviderServiceValidationError(
                "paid endpoints must use fixed_per_call pricing",
            )
        if pricing.amount_minor is None or pricing.currency is None:
            raise ProviderServiceValidationError(
                "fixed_per_call pricing requires amount_minor and currency",
            )

        self._pricing_repo.upsert_fixed_per_call(
            endpoint,
            amount_minor=pricing.amount_minor,
            currency=pricing.currency,
        )
