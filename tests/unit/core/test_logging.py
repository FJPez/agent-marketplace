import json
import logging
import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from starlette.datastructures import Headers

from app.core.logging import (
    DURATION_MS_FIELD,
    EVENT_FIELD,
    METHOD_FIELD,
    PATH_FIELD,
    REQUEST_ID_FIELD,
    REQUEST_ID_HEADER,
    SERVICE_ID_FIELD,
    STATUS_CODE_FIELD,
    bind_request_id,
    build_event_context,
    build_log_context,
    configure_logging,
    reset_request_id,
    resolve_request_id,
)

SECRET = "sk-live-4f9a7c"


@pytest.fixture
def app_logger() -> Iterator[logging.Logger]:
    """The `app` logger, with its handlers and level restored after the test."""
    logger = logging.getLogger("app")
    handlers, level = logger.handlers[:], logger.level
    yield logger
    logger.handlers = handlers
    logger.setLevel(level)


def _json_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


@pytest.mark.parametrize(
    "request_id",
    ["incoming-request-id", "0af7651916cd43dd8448eb211c80319c", "trace.id:1_2", "a" * 128],
)
def test_resolve_request_id_keeps_a_valid_client_value(request_id: str) -> None:
    assert resolve_request_id(request_id) == request_id


@pytest.mark.parametrize(
    "request_id",
    [None, "", "   ", "a" * 129, "abc\ndef", 'abc","level":"CRITICAL', "abc def", "café"],
    ids=["missing", "empty", "blank", "too_long", "newline", "json", "space", "non_ascii"],
)
def test_resolve_request_id_generates_a_uuid_for_a_missing_or_invalid_value(
    request_id: str | None,
) -> None:
    generated = resolve_request_id(request_id)

    assert str(uuid.UUID(generated)) == generated


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


def test_build_event_context_uses_bound_request_id_and_extra_fields() -> None:
    token = bind_request_id("req-456")
    try:
        context = build_event_context("service.published", service_id=9)
    finally:
        reset_request_id(token)

    assert context == {
        EVENT_FIELD: "service.published",
        REQUEST_ID_FIELD: "req-456",
        SERVICE_ID_FIELD: 9,
    }


def test_configure_logging_writes_each_app_record_once_as_a_json_line(
    app_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    # create_app applies the configuration again every time it builds an app.
    configure_logging("INFO")
    logger = app_logger.getChild("tests")
    token = bind_request_id("req-789")
    try:
        logger.debug("below the configured level")
        logger.info("line one\nline two", extra={SERVICE_ID_FIELD: 7})
    finally:
        reset_request_id(token)

    (entry,) = _json_lines(capsys)
    assert datetime.fromisoformat(str(entry.pop("time"))).tzinfo is not None
    assert entry == {
        "level": "INFO",
        "logger": "app.tests",
        "message": "line one\nline two",
        REQUEST_ID_FIELD: "req-789",
        SERVICE_ID_FIELD: 7,
    }


@pytest.mark.parametrize(
    ("message", "args", "extra", "expected"),
    [
        pytest.param(
            "upstream headers %s",
            ({"Authorization": f"Bearer {SECRET}", "Accept": "application/json"},),
            {},
            {
                "message": (
                    "upstream headers {'Authorization': [REDACTED], 'Accept': 'application/json'}"
                ),
            },
            id="headers_dict_in_message",
        ),
        pytest.param(
            "sent PAYMENT-SIGNATURE: %s to the facilitator",
            (SECRET,),
            {},
            {"message": "sent PAYMENT-SIGNATURE: [REDACTED]"},
            id="header_line_in_message",
        ),
        pytest.param(
            "forwarding",
            (),
            {"headers": {"Cookie": f"session={SECRET}", "Accept": "application/json"}},
            {"headers": {"Cookie": "[REDACTED]", "Accept": "application/json"}},
            id="headers_dict_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"headers": Headers({"x-payment": SECRET, "accept": "application/json"})},
            {"headers": {"x-payment": "[REDACTED]", "accept": "application/json"}},
            id="starlette_headers_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"detail": f"authorization={SECRET}"},
            {"detail": "authorization=[REDACTED]"},
            id="header_in_extra_text",
        ),
        pytest.param(
            "forwarding",
            (),
            {"headers": [("Authorization", f"Bearer {SECRET}"), ("Accept", "application/json")]},
            {"headers": [["Authorization", "[REDACTED]"], ["Accept", "application/json"]]},
            id="list_of_str_pairs_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {
                "headers": (
                    (b"authorization", f"Bearer {SECRET}".encode()),
                    (b"accept", b"application/json"),
                )
            },
            {
                "headers": [
                    ["b'authorization'", "[REDACTED]"],
                    ["b'accept'", "b'application/json'"],
                ]
            },
            id="tuple_of_bytes_pairs_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"authorization": f"Bearer {SECRET}"},
            {"authorization": "[REDACTED]"},
            id="flat_authorization_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"payment_signature": SECRET},
            {"payment_signature": "[REDACTED]"},
            id="flat_payment_signature_extra",
        ),
    ],
)
def test_configure_logging_redacts_sensitive_header_values(
    app_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
    message: str,
    args: tuple[object, ...],
    extra: dict[str, object],
    expected: dict[str, object],
) -> None:
    configure_logging("INFO")

    app_logger.getChild("tests").info(message, *args, extra=extra)

    (entry,) = _json_lines(capsys)
    assert SECRET not in json.dumps(entry)
    assert expected.items() <= entry.items()


def test_configure_logging_keeps_standard_fields_when_extra_uses_their_names(
    app_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")

    app_logger.getChild("tests").info(
        "forwarding",
        extra={"level": "CRITICAL", "logger": "evil", "time": "not-a-time"},
    )

    (entry,) = _json_lines(capsys)
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.tests"
    assert datetime.fromisoformat(str(entry["time"])).tzinfo is not None


def test_configure_logging_redacts_sensitive_header_values_in_exceptions(
    app_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")

    try:
        raise RuntimeError(f"facilitator rejected X-PAYMENT: {SECRET}")
    except RuntimeError:
        app_logger.getChild("tests").exception("settle failed")

    (entry,) = _json_lines(capsys)
    assert SECRET not in json.dumps(entry)
    assert "RuntimeError: facilitator rejected X-PAYMENT: [REDACTED]" in str(entry["exception"])


def test_configure_logging_keeps_a_malformed_record_without_raising(
    app_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")

    app_logger.getChild("tests").info("sent %s to %s", f"Authorization: {SECRET}")

    (entry,) = _json_lines(capsys)
    assert entry["message"] == "sent %s to %s"
