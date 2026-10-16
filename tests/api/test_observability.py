from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.logs import LoggedEntries
from tests.fixtures.settings import SettingsEnvFactory

from app.api.deps.rate_limit import get_rate_limits_backend
from app.core.logging import configure_logging
from app.core.request_id import REQUEST_ID_HEADER
from app.main import create_app


def test_health_echoes_provided_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "request-123"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "request-123"


def test_health_generates_request_id_when_header_is_absent(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert str(UUID(response.headers[REQUEST_ID_HEADER])) == response.headers[REQUEST_ID_HEADER]


@pytest.mark.parametrize(
    "request_id",
    [pytest.param("a" * 129, id="too_long"), pytest.param('x","level":"CRITICAL', id="json")],
)
def test_invalid_request_id_is_replaced_by_a_generated_one(
    client: TestClient,
    logged_entries: LoggedEntries,
    request_id: str,
) -> None:
    configure_logging("INFO")

    response = client.get("/health", headers={REQUEST_ID_HEADER: request_id})

    generated = response.headers[REQUEST_ID_HEADER]
    assert str(UUID(generated)) == generated
    (entry,) = logged_entries()
    assert entry["request_id"] == generated


def test_health_logs_request_context(
    client: TestClient,
    logged_entries: LoggedEntries,
) -> None:
    configure_logging("INFO")

    response = client.get("/health", headers={REQUEST_ID_HEADER: "request-123"})

    assert response.status_code == 200
    (entry,) = logged_entries()
    assert {
        "logger": "app.core.observability",
        "message": "request completed",
        "request_id": "request-123",
        "method": "GET",
        "path": "/health",
        "status_code": 200,
    }.items() <= entry.items()
    assert isinstance(entry["duration_ms"], int)


def test_failing_request_logs_exception_context(logged_entries: LoggedEntries) -> None:
    app = create_app()

    @app.get("/boom")
    def read_boom() -> None:
        raise RuntimeError("database password is hunter2")

    # The client re-raises anything that escapes the app: the app must handle the error
    # itself, so nothing reaches Starlette's ServerErrorMiddleware and uvicorn's logger.
    with TestClient(app) as client:
        response = client.get("/boom", headers={REQUEST_ID_HEADER: "request-123"})

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "/problems/internal_error",
        "status": 500,
        "detail": "an unexpected error occurred",
    }
    assert "hunter2" not in response.text
    assert response.headers[REQUEST_ID_HEADER] == "request-123"

    (entry,) = [entry for entry in logged_entries() if entry["level"] == "ERROR"]
    assert {
        "logger": "app.core.observability",
        "message": "request failed",
        "request_id": "request-123",
        "method": "GET",
        "path": "/boom",
    }.items() <= entry.items()


def test_rate_limited_request_carries_request_id_and_is_logged(
    settings_env_factory: SettingsEnvFactory,
    logged_entries: LoggedEntries,
) -> None:
    settings_env_factory(env={"APP_API_RATE_LIMIT": "1/minute"})
    app = create_app()

    with TestClient(app) as client:
        # Without its query parameter the first request is counted, then refused as
        # invalid before it touches the database.
        client.get("/v1/auth/nonce")
        response = client.get("/v1/auth/nonce", headers={REQUEST_ID_HEADER: "request-429"})

    assert response.status_code == 429
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers[REQUEST_ID_HEADER] == "request-429"

    (entry,) = [entry for entry in logged_entries() if entry.get("request_id") == "request-429"]
    assert {
        "message": "request completed",
        "path": "/v1/auth/nonce",
        "status_code": 429,
    }.items() <= entry.items()


class _UnavailableRateLimitsBackend:
    async def hit(self, limit_value: str, *, key: str, scope: str) -> bool:
        msg = "rate limit store unavailable at redis://:hunter2@cache:6379"
        raise ConnectionError(msg)


def test_rate_limit_store_outage_renders_internal_error_problem(
    logged_entries: LoggedEntries,
) -> None:
    app = create_app()
    app.dependency_overrides[get_rate_limits_backend] = _UnavailableRateLimitsBackend

    with TestClient(app) as client:
        response = client.get("/v1/services", headers={REQUEST_ID_HEADER: "request-500"})

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "request-500"
    assert response.json()["type"] == "/problems/internal_error"
    assert "hunter2" not in response.text

    (entry,) = [entry for entry in logged_entries() if entry["level"] == "ERROR"]
    assert entry["request_id"] == "request-500"
