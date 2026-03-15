from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models.service import Service
from app.repositories.provider_profile_repo import ProviderProfileRepository
from app.repositories.service_repo import ServiceRepository
from app.services.moderation_service import ModerationService, ServiceUnavailableError
from app.services.provider_service_errors import (
    ProviderServiceNotFoundError,
    ProviderServiceStateError,
    ProviderServiceValidationError,
)
from app.services.service_health_service import (
    ServiceHealthCheckFailedError,
    ServiceHealthService,
)


def validate_service_for_publish(service: Service) -> None:
    if not service.endpoints:
        raise ProviderServiceValidationError(
            "service must define at least one endpoint before publish",
        )

    enabled_endpoints = [endpoint for endpoint in service.endpoints if endpoint.is_enabled]
    if not enabled_endpoints:
        raise ProviderServiceValidationError(
            "service must enable at least one endpoint before publish",
        )

    for endpoint in enabled_endpoints:
        if endpoint.upstream is None:
            raise ProviderServiceValidationError(
                f"enabled endpoint '{endpoint.key}' must define upstream before publish",
            )
        if endpoint.access_mode is AccessMode.PAID and (
            endpoint.pricing is None
            or endpoint.pricing.pricing_type is not PricingModelType.FIXED_PER_CALL
        ):
            raise ProviderServiceValidationError(
                f"paid endpoint '{endpoint.key}' must define fixed_per_call pricing before publish",
            )


class PublishService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._provider_profile_repo = ProviderProfileRepository(session)
        self._service_repo = ServiceRepository(session)
        self._service_health_service = ServiceHealthService(session)
        self._moderation_service = ModerationService(session)

    async def publish_service(
        self,
        actor: ActorContext,
        *,
        service_id: int,
    ) -> Service:
        await self._require_provider_profile(actor.account_id)
        service = await self._service_repo.get_owned_for_update(
            service_id=service_id,
            provider_account_id=actor.account_id,
        )
        if service is None:
            raise ProviderServiceNotFoundError("service not found")
        if service.lifecycle is not ServiceLifecycle.DRAFT:
            raise ProviderServiceStateError("service is not publishable outside draft")
        try:
            await self._moderation_service.ensure_service_publishable(service.id)
        except ServiceUnavailableError as exc:
            raise ProviderServiceStateError(f"service is {exc.state.value}") from exc

        validate_service_for_publish(service)
        try:
            await self._service_health_service.ensure_publish_ready(service_id=service.id)
        except ServiceHealthCheckFailedError as exc:
            raise ProviderServiceValidationError(
                "service failed latest publish-readiness health check",
            ) from exc
        self._service_repo.set_lifecycle(service, lifecycle=ServiceLifecycle.ACTIVE)
        await self._session.commit()

        published = await self._service_repo.get_owned(
            service_id=service_id,
            provider_account_id=actor.account_id,
        )
        if published is None:
            raise ProviderServiceNotFoundError("service not found")
        return published

    async def _require_provider_profile(self, account_id: int) -> None:
        profile = await self._provider_profile_repo.get_by_account_id(account_id)
        if profile is None:
            raise ProviderServiceNotFoundError("provider profile not found")
