"""Pure publish-readiness rules for the provider service graph."""

from collections.abc import Mapping

from app.core.enums import AccessMode
from app.core.errors import InvalidInputError
from app.db.models.service import Service


def _has_hmac_auth_config(config: Mapping[str, object]) -> bool:
    auth = config.get("auth")
    if not isinstance(auth, dict):
        return False
    auth_map = {str(key): value for key, value in auth.items()}
    return (
        auth_map.get("type") == "hmac_sha256"
        and isinstance(auth_map.get("key_id"), str)
        and isinstance(auth_map.get("secret"), str)
    )


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
        if not _has_hmac_auth_config(endpoint.upstream.config):
            raise InvalidInputError(
                f"enabled endpoint '{endpoint.key}' must define hmac auth config before publish",
            )
        if endpoint.access_mode is AccessMode.PAID and endpoint.price is None:
            raise InvalidInputError(
                f"paid endpoint '{endpoint.key}' must define a price before publish",
            )
