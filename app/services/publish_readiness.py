from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccessMode, PricingModelType, ServiceHealthStatus
from app.db.models.service import Service
from app.integrations.provider_gateway.signing import get_hmac_auth_config
from app.repositories.service_repo import ServiceRepository
from app.services.provider_service_errors import ProviderServiceValidationError
from app.services.service_health_service import ServiceHealthOutcome

PUBLISH_READINESS_PASS_SUMMARY = "service is publish-ready"


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
        if get_hmac_auth_config(endpoint.upstream.config) is None:
            raise ProviderServiceValidationError(
                f"enabled endpoint '{endpoint.key}' must define hmac auth config before publish",
            )
        if endpoint.access_mode is AccessMode.PAID and (
            endpoint.pricing is None
            or endpoint.pricing.pricing_type is not PricingModelType.FIXED_PER_CALL
        ):
            raise ProviderServiceValidationError(
                f"paid endpoint '{endpoint.key}' must define fixed_per_call pricing before publish",
            )


class PublishReadinessChecker:
    def __init__(self, session: AsyncSession) -> None:
        self._service_repo = ServiceRepository(session)

    async def run(self, *, service_id: int) -> ServiceHealthOutcome:
        service = await self._service_repo.get_by_id(service_id=service_id)
        if service is None:
            return ServiceHealthOutcome(
                status=ServiceHealthStatus.FAIL,
                summary="service not found",
            )

        try:
            validate_service_for_publish(service)
        except ProviderServiceValidationError as exc:
            return ServiceHealthOutcome(
                status=ServiceHealthStatus.FAIL,
                summary=str(exc),
            )

        return ServiceHealthOutcome(
            status=ServiceHealthStatus.PASS,
            summary=PUBLISH_READINESS_PASS_SUMMARY,
            details={
                "enabled_endpoint_count": len(
                    [endpoint for endpoint in service.endpoints if endpoint.is_enabled]
                ),
            },
        )
