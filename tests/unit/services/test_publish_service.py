from typing import TYPE_CHECKING, cast

import pytest

from app.core.enums import AccessMode, ServiceLifecycle
from app.db.models.endpoint_price import EndpointPrice
from app.db.models.provider_upstream import ProviderUpstream
from app.db.models.service import Service
from app.db.models.service_endpoint import ServiceEndpoint
from app.repositories.service_repo import ServiceRepository
from app.services.provider_service_errors import ProviderServiceValidationError
from app.services.publish_readiness import (
    PUBLISH_READINESS_PASS_SUMMARY,
    PublishReadinessChecker,
    validate_service_for_publish,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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
    price: EndpointPrice | None = None,
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
            config={
                "auth": {
                    "type": "hmac_sha256",
                    "key_id": "gateway-key",
                    "secret": "super-secret",
                },
            },
        )
    endpoint.price = price
    return endpoint


def _build_fixed_price() -> EndpointPrice:
    return EndpointPrice(
        endpoint_id=1,
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
                price=_build_fixed_price(),
            ),
        ],
    )

    validate_service_for_publish(service)


def test_validate_service_for_publish_rejects_missing_hmac_auth_config() -> None:
    endpoint = _build_endpoint()
    assert endpoint.upstream is not None
    endpoint.upstream.config = {}
    service = _build_service(endpoints=[endpoint])

    with pytest.raises(
        ProviderServiceValidationError,
        match="must define hmac auth config before publish",
    ):
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


@pytest.mark.asyncio
async def test_publish_readiness_checker_returns_fail_summary_for_invalid_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _build_service(endpoints=[])

    async def fake_get_by_id(
        self: ServiceRepository,
        *,
        service_id: int,
    ) -> Service:
        _ = self
        _ = service_id
        return service

    monkeypatch.setattr(ServiceRepository, "get_by_id", fake_get_by_id)

    checker = PublishReadinessChecker(cast("AsyncSession", object()))

    outcome = await checker.run(service_id=1)

    assert outcome.status.value == "fail"
    assert outcome.summary == "service must define at least one endpoint before publish"


@pytest.mark.asyncio
async def test_publish_readiness_checker_returns_pass_for_ready_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _build_service(endpoints=[_build_endpoint(price=_build_fixed_price())])

    async def fake_get_by_id(
        self: ServiceRepository,
        *,
        service_id: int,
    ) -> Service:
        _ = self
        _ = service_id
        return service

    monkeypatch.setattr(ServiceRepository, "get_by_id", fake_get_by_id)

    checker = PublishReadinessChecker(cast("AsyncSession", object()))

    outcome = await checker.run(service_id=1)

    assert outcome.status.value == "pass"
    assert outcome.summary == PUBLISH_READINESS_PASS_SUMMARY
    assert outcome.details == {"enabled_endpoint_count": 1}
