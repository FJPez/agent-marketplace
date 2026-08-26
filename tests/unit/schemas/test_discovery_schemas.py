import pytest
from pydantic import ValidationError

from app.core.enums import AccessMode, ServiceLifecycle
from app.core.service_fields import SLUG_MAX_LENGTH
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.discovery import (
    SERVICE_ID_MAX,
    PublicEndpointPricing,
    PublicServiceDetail,
    parse_public_service_ref,
)


def _build_endpoint(
    *,
    key: str,
    access_mode: AccessMode = AccessMode.FREE,
    is_enabled: bool = True,
) -> ServiceEndpoint:
    return ServiceEndpoint(
        service_id=1,
        key=key,
        name=f"{key} name",
        summary=f"{key} summary",
        description=f"{key} description",
        access_mode=access_mode,
        request_schema={"type": "object"},
        response_schema={"type": "object"},
        timeout_seconds=30,
        is_enabled=is_enabled,
    )


def _build_service(*, endpoints: list[ServiceEndpoint]) -> Service:
    service = Service(
        provider_account_id=1,
        slug="translation-service",
        name="Translation Service",
        summary="Summary",
        description=None,
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    service.id = 1
    service.endpoints = endpoints
    service.tags = []
    return service


def test_public_service_detail_filters_disabled_endpoints() -> None:
    service = _build_service(
        endpoints=[
            _build_endpoint(key="translate", is_enabled=True),
            _build_endpoint(key="disabled", is_enabled=False),
        ],
    )

    result = PublicServiceDetail.from_model(service)

    assert [endpoint.key for endpoint in result.endpoints] == ["translate"]


def test_public_endpoint_pricing_uses_free_fallback_for_free_endpoints() -> None:
    endpoint = _build_endpoint(key="translate", access_mode=AccessMode.FREE)

    result = PublicEndpointPricing.from_model(endpoint)

    assert result.pricing_type == "free"
    assert result.amount_minor is None
    assert result.currency is None


def test_parse_public_service_ref_reads_numeric_identifiers_as_ids() -> None:
    ref = parse_public_service_ref("42")

    assert ref.id == 42
    assert ref.slug is None


def test_parse_public_service_ref_reads_non_numeric_identifiers_as_slugs() -> None:
    ref = parse_public_service_ref("translation-service")

    assert ref.id is None
    assert ref.slug == "translation-service"


def test_parse_public_service_ref_accepts_maximum_length_slug() -> None:
    longest_slug = "a" * SLUG_MAX_LENGTH

    ref = parse_public_service_ref(longest_slug)

    assert ref.slug == longest_slug


def test_parse_public_service_ref_accepts_maximum_service_id() -> None:
    ref = parse_public_service_ref(str(SERVICE_ID_MAX))

    assert ref.id == SERVICE_ID_MAX


@pytest.mark.parametrize(
    "identifier",
    [
        "Translation-Service",
        "translation service",
        "translation_service",
        "-leading-dash",
        "a" * (SLUG_MAX_LENGTH + 1),
        str(SERVICE_ID_MAX + 1),
        "0",
        "",
    ],
)
def test_parse_public_service_ref_rejects_malformed_identifiers(identifier: str) -> None:
    with pytest.raises(ValidationError):
        parse_public_service_ref(identifier)
