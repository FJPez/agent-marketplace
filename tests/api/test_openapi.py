from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.core.config import get_settings
from app.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

RETIRED_PATH_PREFIXES = (
    "/v1/invoke",
    "/v1/invocations",
    "/v1/provider/earnings",
    "/v1/provider/ledger",
    "/v1/provider/payouts",
)
PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}},
}


@pytest.fixture
def openapi_document() -> Iterator[dict[str, Any]]:
    get_settings.cache_clear()
    try:
        yield create_app().openapi()
    finally:
        get_settings.cache_clear()


@pytest.fixture
def openapi_paths(openapi_document: dict[str, Any]) -> dict[str, Any]:
    return openapi_document["paths"]


def test_openapi_documents_provider_service_creation(
    openapi_paths: dict[str, Any],
) -> None:
    provider_spec = openapi_paths["/v1/provider/services"]["post"]

    assert provider_spec["summary"] == "Create a draft provider service"
    assert provider_spec["requestBody"]["content"]["application/json"]["examples"]


def test_openapi_no_longer_documents_retired_execution_routes(
    openapi_paths: dict[str, Any],
) -> None:
    retired = [
        path
        for path in openapi_paths
        if path.startswith(RETIRED_PATH_PREFIXES) or path.endswith("/quote")
    ]

    assert retired == []


def test_openapi_documents_every_error_response_as_a_problem(
    openapi_document: dict[str, Any],
) -> None:
    operations = [
        operation
        for path_item in openapi_document["paths"].values()
        for operation in path_item.values()
    ]
    error_responses = [
        response
        for operation in operations
        for status_code, response in operation["responses"].items()
        if not status_code.startswith(("2", "3"))
    ]
    schemas = openapi_document["components"]["schemas"]

    assert all("default" in operation["responses"] for operation in operations)
    assert all(response["content"] == PROBLEM_CONTENT for response in error_responses)
    assert schemas["Problem"]["required"] == ["type", "title", "status", "detail"]
    assert "HTTPValidationError" not in schemas
