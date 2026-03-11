import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.logging import REQUEST_ID_HEADER
from app.main import create_app


def test_health_echoes_provided_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "request-123"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "request-123"


def test_health_generates_request_id_when_header_is_absent(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert (
        str(uuid.UUID(response.headers[REQUEST_ID_HEADER])) == response.headers[REQUEST_ID_HEADER]
    )


def test_health_logs_request_context(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.core.observability"):
        response = client.get("/health", headers={REQUEST_ID_HEADER: "request-123"})

    assert response.status_code == 200

    record = next(record for record in caplog.records if record.name == "app.core.observability")
    assert record.request_id == "request-123"
    assert record.method == "GET"
    assert record.path == "/health"
    assert record.status_code == 200
    assert record.duration_ms >= 0


def test_failing_request_logs_exception_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()

    @app.get("/boom")
    def read_boom() -> None:
        raise RuntimeError("boom")

    with (
        caplog.at_level(logging.ERROR, logger="app.core.observability"),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/boom", headers={REQUEST_ID_HEADER: "request-123"})

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers[REQUEST_ID_HEADER] == "request-123"

    record = next(record for record in caplog.records if record.name == "app.core.observability")
    assert record.request_id == "request-123"
    assert record.method == "GET"
    assert record.path == "/boom"
