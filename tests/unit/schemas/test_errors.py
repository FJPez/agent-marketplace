import pytest
from pydantic import ValidationError

from app.schemas.errors import ApiError, ErrorDetail, ErrorResponse


def test_error_response_serializes_nested_error_payload() -> None:
    response = ErrorResponse(
        error=ApiError(
            code="quote_mismatch",
            message="Quote does not match the request body.",
            details=[
                ErrorDetail(
                    field="request_hash",
                    message="Request hash does not match the bound quote.",
                )
            ],
        )
    )

    assert response.model_dump(mode="json") == {
        "error": {
            "code": "quote_mismatch",
            "message": "Quote does not match the request body.",
            "details": [
                {
                    "field": "request_hash",
                    "message": "Request hash does not match the bound quote.",
                }
            ],
        }
    }


def test_error_response_allows_details_to_be_omitted() -> None:
    response = ErrorResponse(
        error=ApiError(
            code="not_found",
            message="Service was not found.",
        )
    )

    assert response.model_dump(mode="json") == {
        "error": {
            "code": "not_found",
            "message": "Service was not found.",
            "details": None,
        }
    }


def test_error_detail_requires_message() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ErrorDetail.model_validate({"field": "request_hash"})

    assert exc_info.value.errors()[0]["loc"] == ("message",)
