import pytest
from tests.fixtures.settings import TEST_PRICE_TERMS

from app.core.enums import AccessMode, ServiceLifecycle
from app.core.errors import InvalidInputError
from app.db.models.listing_price import ListingPrice
from app.db.models.provider_upstream import ProviderUpstream
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.services.publish_readiness import validate_service_for_publish


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
    price: ListingPrice | None = None,
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
        )
    endpoint.current_price = price
    return endpoint


def test_validate_service_for_publish_rejects_service_without_endpoints() -> None:
    service = _build_service(endpoints=[])

    with pytest.raises(
        InvalidInputError,
        match="service must define at least one endpoint before publish",
    ):
        validate_service_for_publish(service, has_signing_secret=True)


def test_validate_service_for_publish_rejects_enabled_endpoint_without_upstream() -> None:
    service = _build_service(endpoints=[_build_endpoint(with_upstream=False)])

    with pytest.raises(
        InvalidInputError,
        match="must define upstream before publish",
    ):
        validate_service_for_publish(service, has_signing_secret=True)


def test_validate_service_for_publish_rejects_paid_endpoint_without_price() -> None:
    service = _build_service(endpoints=[_build_endpoint(access_mode=AccessMode.PAID)])

    with pytest.raises(
        InvalidInputError,
        match="must define a price before publish",
    ):
        validate_service_for_publish(service, has_signing_secret=True)


def test_validate_service_for_publish_accepts_enabled_paid_endpoint_with_a_price() -> None:
    service = _build_service(
        endpoints=[
            _build_endpoint(
                access_mode=AccessMode.PAID,
                price=ListingPrice(endpoint_id=1, version=1, amount=10_000, **TEST_PRICE_TERMS),
            ),
        ],
    )

    validate_service_for_publish(service, has_signing_secret=True)


def test_validate_service_for_publish_rejects_service_with_only_disabled_endpoints() -> None:
    service = _build_service(
        endpoints=[_build_endpoint(is_enabled=False)],
    )

    with pytest.raises(
        InvalidInputError,
        match="service must enable at least one endpoint before publish",
    ):
        validate_service_for_publish(service, has_signing_secret=True)


def test_validate_service_for_publish_rejects_a_provider_without_a_signing_secret() -> None:
    service = _build_service(endpoints=[_build_endpoint()])

    with pytest.raises(
        InvalidInputError,
        match="provider must create a signing secret before publish",
    ):
        validate_service_for_publish(service, has_signing_secret=False)


def test_validate_service_for_publish_checks_endpoints_before_the_signing_secret() -> None:
    service = _build_service(endpoints=[_build_endpoint(with_upstream=False)])

    with pytest.raises(InvalidInputError, match="must define upstream before publish"):
        validate_service_for_publish(service, has_signing_secret=False)
