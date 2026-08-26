"""Pure publish-readiness rules for the provider service graph."""

from app.core.enums import AccessMode
from app.core.errors import InvalidInputError
from app.db.models.service import Service
from app.integrations.provider_gateway.signing import get_hmac_auth_config

PUBLISH_READINESS_PASS_SUMMARY = "service is publish-ready"


def validate_service_for_publish(service: Service) -> None:
    if not service.endpoints:
        raise InvalidInputError(
            "service must define at least one endpoint before publish",
        )

    enabled_endpoints = [endpoint for endpoint in service.endpoints if endpoint.is_enabled]
    if not enabled_endpoints:
        raise InvalidInputError(
            "service must enable at least one endpoint before publish",
        )

    for endpoint in enabled_endpoints:
        if endpoint.upstream is None:
            raise InvalidInputError(
                f"enabled endpoint '{endpoint.key}' must define upstream before publish",
            )
        if get_hmac_auth_config(endpoint.upstream.config) is None:
            raise InvalidInputError(
                f"enabled endpoint '{endpoint.key}' must define hmac auth config before publish",
            )
        if endpoint.access_mode is AccessMode.PAID and endpoint.price is None:
            raise InvalidInputError(
                f"paid endpoint '{endpoint.key}' must define a price before publish",
            )
