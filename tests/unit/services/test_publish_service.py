import pytest

from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models.pricing_model import PricingModel
from app.db.models.provider_upstream import ProviderUpstream
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.services.provider_service_errors import ProviderServiceValidationError
from app.services.publish_service import validate_service_for_publish


def _build_service(*, endpoints: list[ServiceEndpoint]) -> Service:
    service = Service(
        provider_account_id=1,
        slug="translation-service",
        name="Translation Service",
        summary="Summary",
        description=None,
        lifecycle=ServiceLifecycle.DRAFT,
    )
    service.endpoints = endpoints
    return service


def _build_endpoint(
    *,
    key: str = "translate",
    access_mode: AccessMode = AccessMode.FREE,
    is_enabled: bool = True,
    with_upstream: bool = True,
    pricing: PricingModel | None = None,
) -> ServiceEndpoint:
    endpoint = ServiceEndpoint(
        service_id=1,
        key=key,
        name="Translate",
        summary=None,
        description=None,
        access_mode=access_mode,
        request_schema={"type": "object"},
        response_schema={"type": "object"},
        timeout_seconds=30,
        is_enabled=is_enabled,
    )
    if with_upstream:
        endpoint.upstream = ProviderUpstream(
            endpoint_id=1,
            base_url="https://provider.internal",
            path="/translate",
            http_method="POST",
            config={},
        )
    endpoint.pricing = pricing
    return endpoint


def _build_fixed_price() -> PricingModel:
    return PricingModel(
        endpoint_id=1,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
    )


def test_validate_service_for_publish_rejects_service_without_endpoints() -> None:
    service = _build_service(endpoints=[])

    with pytest.raises(
        ProviderServiceValidationError,
        match="service must define at least one endpoint before publish",
    ):
        validate_service_for_publish(service)


def test_validate_service_for_publish_rejects_enabled_endpoint_without_upstream() -> None:
    service = _build_service(endpoints=[_build_endpoint(with_upstream=False)])

    with pytest.raises(
        ProviderServiceValidationError,
        match="must define upstream before publish",
    ):
        validate_service_for_publish(service)


def test_validate_service_for_publish_rejects_paid_endpoint_without_fixed_price() -> None:
    service = _build_service(endpoints=[_build_endpoint(access_mode=AccessMode.PAID)])

    with pytest.raises(
        ProviderServiceValidationError,
        match="must define fixed_per_call pricing before publish",
    ):
        validate_service_for_publish(service)


def test_validate_service_for_publish_accepts_enabled_paid_endpoint_with_fixed_price() -> None:
    service = _build_service(
        endpoints=[
            _build_endpoint(
                access_mode=AccessMode.PAID,
                pricing=_build_fixed_price(),
            ),
        ],
    )

    validate_service_for_publish(service)


def test_validate_service_for_publish_rejects_service_with_only_disabled_endpoints() -> None:
    service = _build_service(
        endpoints=[_build_endpoint(is_enabled=False)],
    )

    with pytest.raises(
        ProviderServiceValidationError,
        match="service must enable at least one endpoint before publish",
    ):
        validate_service_for_publish(service)
