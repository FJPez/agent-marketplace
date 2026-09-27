"""Pure publish-readiness rules for the provider service graph."""

from app.core.enums import AccessMode
from app.core.errors import InvalidInputError
from app.db.models.service import Service


def validate_service_for_publish(service: Service, *, has_signing_secret: bool) -> None:
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
        if endpoint.access_mode is AccessMode.PAID and endpoint.current_price is None:
            raise InvalidInputError(
                f"paid endpoint '{endpoint.key}' must define a price before publish",
            )

    if not has_signing_secret:
        raise InvalidInputError("provider must create a signing secret before publish")
