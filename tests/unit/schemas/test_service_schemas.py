import pytest
from pydantic import ValidationError

from app.core.enums import AccessMode
from app.schemas.service import (
    EndpointCreateRequest,
    EndpointUpdateRequest,
    ServiceCreateRequest,
    ServiceUpdateRequest,
)


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


def test_endpoint_update_request_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        EndpointUpdateRequest.model_validate({"name": "Renamed", "unknown_field": "x"})


@pytest.mark.parametrize(
    "field",
    ["name", "access_mode", "request_schema", "response_schema", "timeout_seconds", "is_enabled"],
)
def test_endpoint_update_request_rejects_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError) as error:
        EndpointUpdateRequest.model_validate({field: None})

    first_error = error.value.errors()[0]
    assert first_error["loc"] == (field,)
    assert "cannot be null" in first_error["msg"]


@pytest.mark.parametrize("field", ["summary", "description", "pricing"])
def test_endpoint_update_request_accepts_explicit_null_for_clearable_field(field: str) -> None:
    request = EndpointUpdateRequest.model_validate({field: None})

    assert getattr(request, field) is None
    assert request.model_fields_set == {field}


def test_endpoint_update_request_omits_unsent_fields_from_fields_set() -> None:
    request = EndpointUpdateRequest.model_validate({"timeout_seconds": 45})

    assert request.model_fields_set == {"timeout_seconds"}
    assert request.name is None


def test_endpoint_update_request_rejects_empty_payload() -> None:
    with pytest.raises(ValidationError, match="at least one field must be provided"):
        EndpointUpdateRequest.model_validate({})


def test_endpoint_update_request_rejects_boolean_timeout_seconds() -> None:
    with pytest.raises(ValidationError):
        EndpointUpdateRequest.model_validate({"timeout_seconds": True})


def test_endpoint_update_request_rejects_integer_is_enabled() -> None:
    with pytest.raises(ValidationError):
        EndpointUpdateRequest.model_validate({"is_enabled": 1})


def test_endpoint_update_request_schema_hides_null_from_non_clearable_fields() -> None:
    schema = EndpointUpdateRequest.model_json_schema()

    name_schema = schema["properties"]["name"]
    assert "anyOf" not in name_schema
    assert "null" not in str(name_schema.get("type", ""))
    assert "required" not in schema


def test_service_update_request_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ServiceUpdateRequest.model_validate({"name": "Renamed", "unknown_field": "x"})


@pytest.mark.parametrize("field", ["name", "summary"])
def test_service_update_request_rejects_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError) as error:
        ServiceUpdateRequest.model_validate({field: None})

    first_error = error.value.errors()[0]
    assert first_error["loc"] == (field,)
    assert "cannot be null" in first_error["msg"]


def test_service_update_request_accepts_explicit_null_for_description() -> None:
    request = ServiceUpdateRequest.model_validate({"description": None})

    assert request.description is None
    assert request.model_fields_set == {"description"}


def test_service_update_request_rejects_empty_payload() -> None:
    with pytest.raises(ValidationError, match="at least one field must be provided"):
        ServiceUpdateRequest.model_validate({})


def test_service_update_request_omits_unsent_fields_from_fields_set() -> None:
    request = ServiceUpdateRequest.model_validate({"summary": "New summary"})

    assert request.model_fields_set == {"summary"}
    assert request.name is None
    assert request.description is None


def test_service_update_request_schema_hides_null_from_non_clearable_fields() -> None:
    schema = ServiceUpdateRequest.model_json_schema()

    for field in ("name", "summary"):
        field_schema = schema["properties"][field]
        assert "anyOf" not in field_schema
        assert field_schema["type"] == "string"
    description_types = [option["type"] for option in schema["properties"]["description"]["anyOf"]]
    assert "null" in description_types
    assert "required" not in schema
