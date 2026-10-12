import json
import logging
import uuid
from datetime import datetime

import pytest
from tests.fixtures.logs import LoggedEntries

from app.core.logging import (
    bind_request_id,
    configure_logging,
    reset_request_id,
    resolve_request_id,
)

SECRET = "sk-live-4f9a7c"


@pytest.fixture
def logger() -> logging.Logger:
    # Each test configures logging in its body: capsys swaps stdout after fixture setup.
    return logging.getLogger("app.tests")


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


def test_configure_logging_writes_each_app_record_once_as_a_json_line(
    logger: logging.Logger,
    logged_entries: LoggedEntries,
) -> None:
    configure_logging("INFO")
    # create_app applies the configuration again every time it builds an app.
    configure_logging("INFO")
    token = bind_request_id("req-789")
    try:
        logger.debug("below the configured level")
        logger.info("line one\nline two", extra={"service_id": 7, "ratio": 0.5, "cached": True})
    finally:
        reset_request_id(token)

    (entry,) = logged_entries()
    assert datetime.fromisoformat(str(entry.pop("time"))).tzinfo is not None
    assert entry == {
        "level": "INFO",
        "logger": "app.tests",
        "message": "line one\nline two",
        "request_id": "req-789",
        "service_id": 7,
        "ratio": 0.5,
        "cached": True,
    }


@pytest.mark.parametrize(
    ("message", "args", "extra", "expected"),
    [
        pytest.param(
            "sent PAYMENT-SIGNATURE: %s to the facilitator",
            (SECRET,),
            {},
            {"message": "sent PAYMENT-SIGNATURE: [REDACTED]"},
            id="header_line_in_message",
        ),
        pytest.param(
            "upstream headers %s",
            ({"Authorization": f"Bearer {SECRET}", "Accept": "application/json"},),
            {},
            {"message": "upstream headers {'Authorization': [REDACTED]"},
            id="headers_dict_in_message",
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
            {"request": {"headers": {"Cookie": f"session={SECRET}", "Accept": "*/*"}}},
            {"request": "{'headers': {'Cookie': [REDACTED]"},
            id="nested_headers_dict_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"headers": [(b"x-payment", SECRET.encode()), (b"accept", b"*/*")]},
            {"headers": "[(b'x-payment', [REDACTED]"},
            id="raw_header_pairs_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"authorization": f"Bearer {SECRET}"},
            {"authorization": "[REDACTED]"},
            id="extra_named_authorization",
        ),
        pytest.param(
            "forwarding",
            (),
            {"payment_signature": SECRET},
            {"payment_signature": "[REDACTED]"},
            id="extra_named_payment_signature",
        ),
    ],
)
def test_configure_logging_redacts_sensitive_header_values(
    logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
    message: str,
    args: tuple[object, ...],
    extra: dict[str, object],
    expected: dict[str, object],
) -> None:
    configure_logging("INFO")

    logger.info(message, *args, extra=extra)

    (line,) = capsys.readouterr().out.splitlines()
    assert SECRET not in line
    assert expected.items() <= json.loads(line).items()


@pytest.mark.parametrize(
    "message",
    [
        "authorization, cookie and x-payment headers are redacted",
        "authorization failed for account 5",
        "authorization_failed: true and x_payment_id=5",
    ],
)
def test_configure_logging_keeps_prose_that_names_a_sensitive_header(
    logger: logging.Logger,
    logged_entries: LoggedEntries,
    message: str,
) -> None:
    configure_logging("INFO")

    logger.info(message)

    (entry,) = logged_entries()
    assert entry["message"] == message


def test_configure_logging_keeps_standard_fields_when_extra_uses_their_names(
    logger: logging.Logger,
    logged_entries: LoggedEntries,
) -> None:
    configure_logging("INFO")

    logger.info("forwarding", extra={"level": "CRITICAL", "logger": "evil", "time": "not-a-time"})

    (entry,) = logged_entries()
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.tests"
    assert datetime.fromisoformat(str(entry["time"])).tzinfo is not None


def test_configure_logging_redacts_sensitive_header_values_in_exceptions(
    logger: logging.Logger,
    logged_entries: LoggedEntries,
) -> None:
    configure_logging("INFO")

    try:
        raise RuntimeError(f"facilitator rejected X-PAYMENT: {SECRET}")
    except RuntimeError:
        logger.exception("settle failed")

    (entry,) = logged_entries()
    assert SECRET not in json.dumps(entry)
    assert "RuntimeError: facilitator rejected X-PAYMENT: [REDACTED]" in str(entry["exception"])


def test_configure_logging_redacts_the_stack(
    logger: logging.Logger,
    logged_entries: LoggedEntries,
) -> None:
    configure_logging("INFO")
    stack = f'Stack (most recent call last):\n  File "pay.py", line 1\n    X-PAYMENT: {SECRET}'

    logger.handle(
        logger.makeRecord(logger.name, logging.INFO, "pay.py", 1, "paying", (), None, sinfo=stack)
    )

    (entry,) = logged_entries()
    assert entry["stack"] == (
        'Stack (most recent call last):\n  File "pay.py", line 1\n    X-PAYMENT: [REDACTED]'
    )
