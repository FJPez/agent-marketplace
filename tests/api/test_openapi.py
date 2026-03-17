import pytest

from app.core.config import get_settings
from app.main import create_app


def test_openapi_documents_submission_critical_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    get_settings.cache_clear()
    schema = create_app().openapi()

    invoke_spec = schema["paths"]["/v1/invoke/{service_id_or_slug}"]["post"]
    quote_spec = schema["paths"]["/v1/services/{service_id_or_slug}/quote"]["post"]
    provider_spec = schema["paths"]["/v1/provider/services"]["post"]

    assert invoke_spec["summary"] == "Invoke a service endpoint"
    assert "Idempotency-Key" in invoke_spec["description"]
    assert "402" in invoke_spec["responses"]
    assert invoke_spec["responses"]["402"]["headers"]["PAYMENT-REQUIRED"]["description"]
    assert invoke_spec["responses"]["200"]["description"] == "Invocation completed successfully."

    assert quote_spec["summary"] == "Create a quote for a priced endpoint"
    assert "publicly accessible" in quote_spec["description"]
    assert quote_spec["requestBody"]["content"]["application/json"]["examples"]

    assert provider_spec["summary"] == "Create a draft provider service"
    assert provider_spec["requestBody"]["content"]["application/json"]["examples"]
    get_settings.cache_clear()
