import logging
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.core.logging import (
    DURATION_MS_FIELD,
    METHOD_FIELD,
    PATH_FIELD,
    REQUEST_ID_FIELD,
    REQUEST_ID_HEADER,
    STATUS_CODE_FIELD,
)
from app.main import create_app


def _get_str_field(record: logging.LogRecord, field_name: str) -> str:
    value = getattr(record, field_name, None)
    if not isinstance(value, str):
        msg = f"log record field {field_name!r} must be a str"
        raise AssertionError(msg)
    return value


def _get_int_field(record: logging.LogRecord, field_name: str) -> int:
    value = getattr(record, field_name, None)
    if not isinstance(value, int):
        msg = f"log record field {field_name!r} must be an int"
        raise AssertionError(msg)
    return value


@dataclass(slots=True)
class RequestCompletedLog:
    request_id: str
    method: str
    path: str
    status_code: int
    duration_ms: int

    @classmethod
    def from_record(cls, record: logging.LogRecord) -> "RequestCompletedLog":
        return cls(
            request_id=_get_str_field(record, REQUEST_ID_FIELD),
            method=_get_str_field(record, METHOD_FIELD),
            path=_get_str_field(record, PATH_FIELD),
            status_code=_get_int_field(record, STATUS_CODE_FIELD),
            duration_ms=_get_int_field(record, DURATION_MS_FIELD),
        )


@dataclass(slots=True)
class RequestFailedLog:
    request_id: str
    method: str
    path: str

    @classmethod
    def from_record(cls, record: logging.LogRecord) -> "RequestFailedLog":
        return cls(
            request_id=_get_str_field(record, REQUEST_ID_FIELD),
            method=_get_str_field(record, METHOD_FIELD),
            path=_get_str_field(record, PATH_FIELD),
        )


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
    request_log = RequestCompletedLog.from_record(record)
    assert request_log.request_id == "request-123"
    assert request_log.method == "GET"
    assert request_log.path == "/health"
    assert request_log.status_code == 200
    assert request_log.duration_ms >= 0


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
    error_log = RequestFailedLog.from_record(record)
    assert error_log.request_id == "request-123"
    assert error_log.method == "GET"
    assert error_log.path == "/boom"
