import pytest

from app.core.config import get_settings
from app.main import create_app

RETIRED_PATH_PREFIXES = (
    "/v1/invoke",
    "/v1/invocations",
    "/v1/provider/earnings",
    "/v1/provider/ledger",
    "/v1/provider/payouts",
)


def test_openapi_documents_provider_service_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    get_settings.cache_clear()
    schema = create_app().openapi()

    provider_spec = schema["paths"]["/v1/provider/services"]["post"]

    assert provider_spec["summary"] == "Create a draft provider service"
    assert provider_spec["requestBody"]["content"]["application/json"]["examples"]
    get_settings.cache_clear()


def test_openapi_no_longer_documents_retired_execution_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    get_settings.cache_clear()
    paths = create_app().openapi()["paths"]

    retired = [
        path for path in paths if path.startswith(RETIRED_PATH_PREFIXES) or path.endswith("/quote")
    ]

    assert retired == []
    get_settings.cache_clear()
