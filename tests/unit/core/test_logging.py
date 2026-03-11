import uuid

from app.core.logging import (
    DURATION_MS_FIELD,
    METHOD_FIELD,
    PATH_FIELD,
    REQUEST_ID_FIELD,
    REQUEST_ID_HEADER,
    STATUS_CODE_FIELD,
    build_log_context,
    resolve_request_id,
)


def test_resolve_request_id_uses_incoming_header_value() -> None:
    assert resolve_request_id("incoming-request-id") == "incoming-request-id"


def test_resolve_request_id_generates_uuid_when_header_missing_or_blank() -> None:
    missing_request_id = resolve_request_id(None)
    blank_request_id = resolve_request_id("   ")

    assert missing_request_id != blank_request_id
    assert str(uuid.UUID(missing_request_id)) == missing_request_id
    assert str(uuid.UUID(blank_request_id)) == blank_request_id


def test_build_log_context_uses_stable_request_fields() -> None:
    context = build_log_context(
        request_id="req-123",
        method="GET",
        path="/health",
        status_code=200,
        duration_ms=12,
    )

    assert context == {
        REQUEST_ID_FIELD: "req-123",
        METHOD_FIELD: "GET",
        PATH_FIELD: "/health",
        STATUS_CODE_FIELD: 200,
        DURATION_MS_FIELD: 12,
    }
    assert REQUEST_ID_HEADER == "X-Request-ID"
