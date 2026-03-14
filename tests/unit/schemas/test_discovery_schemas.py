from app.core.enums import AccessMode, ServiceLifecycle
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.schemas.discovery import PublicEndpointPricing, PublicServiceDetail


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
