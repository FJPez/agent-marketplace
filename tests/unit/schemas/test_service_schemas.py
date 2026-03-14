import pytest
from pydantic import ValidationError

from app.core.enums import AccessMode
from app.schemas.service import EndpointCreateRequest, ServiceCreateRequest


def test_service_create_request_rejects_numeric_only_slug() -> None:
    with pytest.raises(ValidationError):
        ServiceCreateRequest(
            slug="123",
            name="Numeric Slug Service",
            summary="Summary",
        )


def test_endpoint_create_request_rejects_numeric_only_key() -> None:
    with pytest.raises(ValidationError):
        EndpointCreateRequest(
            key="123",
            name="Numeric Key Endpoint",
            access_mode=AccessMode.FREE,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
        )


def test_service_create_request_accepts_mixed_alphanumeric_slug() -> None:
    request = ServiceCreateRequest(
        slug="service-123",
        name="Translation Service",
        summary="Summary",
    )

    assert request.slug == "service-123"


def test_endpoint_create_request_accepts_mixed_alphanumeric_key() -> None:
    request = EndpointCreateRequest(
        key="translate-123",
        name="Translate",
        access_mode=AccessMode.FREE,
        request_schema={"type": "object"},
        response_schema={"type": "object"},
        timeout_seconds=30,
    )

    assert request.key == "translate-123"
