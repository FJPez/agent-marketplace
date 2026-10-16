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


@pytest.fixture
def openapi_paths() -> Iterator[dict[str, Any]]:
    get_settings.cache_clear()
    try:
        yield create_app().openapi()["paths"]
    finally:
        get_settings.cache_clear()


def test_openapi_documents_provider_service_creation(
    openapi_paths: dict[str, Any],
) -> None:
    provider_spec = openapi_paths["/v1/provider/services"]["post"]

    assert provider_spec["summary"] == "Create a draft provider service"
    assert provider_spec["requestBody"]["content"]["application/json"]["examples"]


def test_openapi_documents_the_bearer_scheme_on_v1_routes_only(
    openapi_paths: dict[str, Any],
) -> None:
    bearer = [{"HTTPBearer": []}]

    assert openapi_paths["/v1/provider/services"]["get"]["security"] == bearer
    # The rate limit reads the token on every /v1 route, so public ones list it too.
    assert openapi_paths["/v1/services"]["get"]["security"] == bearer
    assert "security" not in openapi_paths["/health"]["get"]


def test_openapi_no_longer_documents_retired_execution_routes(
    openapi_paths: dict[str, Any],
) -> None:
    retired = [
        path
        for path in openapi_paths
        if path.startswith(RETIRED_PATH_PREFIXES) or path.endswith("/quote")
    ]

    assert retired == []
