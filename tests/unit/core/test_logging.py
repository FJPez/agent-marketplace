import json
import logging
import uuid
from collections import namedtuple
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime

import httpx
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
    JsonFormatter,
    bind_request_id,
    build_event_context,
    build_log_context,
    configure_logging,
    reset_request_id,
    resolve_request_id,
)

SECRET = "sk-live-4f9a7c"

_HeaderPair = namedtuple("_HeaderPair", ["name", "value"])

# Raw ASGI headers with a repeated name, as a client that sends two Accept headers
# produces; a Starlette `Headers` built from them switches its repr to `raw=[...]`.
_RAW_HEADERS = [
    (b"authorization", f"Bearer {SECRET}".encode()),
    (b"accept", b"application/json"),
    (b"accept", b"text/plain"),
]


@dataclass
class _PaymentHeaders:
    """Renders like a pydantic model or dataclass with header-named fields."""

    payment_signature: str
    x_payment: str


class _HeaderCarrier:
    """An arbitrary object whose `str()` holds a header line."""

    def __str__(self) -> str:
        return f"Cookie: session={SECRET}"


class _UnprintableError(Exception):
    """Raised by `_Unprintable.__str__`, as a lazy-loading repr might raise."""


class _Unprintable:
    def __str__(self) -> str:
        raise _UnprintableError


# A list that contains itself, and a dict that contains itself: redaction must
# terminate on these without a RecursionError, by bounding nesting depth rather
# than tracking object identities.
_SELF_REFERENCING_LIST: list[object] = [("authorization", SECRET)]
_SELF_REFERENCING_LIST.append(_SELF_REFERENCING_LIST)
_SELF_REFERENCING_DICT: dict[str, object] = {"authorization": SECRET}
_SELF_REFERENCING_DICT["self"] = _SELF_REFERENCING_DICT


class _ExplodingMapping(Mapping[str, str]):
    """A Mapping that raises when iterated, to test that redaction fails closed."""

    def __getitem__(self, key: str) -> str:
        raise RuntimeError("boom")

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("boom")

    def __len__(self) -> int:
        return 0


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
            {"headers": [["authorization", "[REDACTED]"], ["accept", "application/json"]]},
            id="tuple_of_bytes_pairs_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"headers": dict(_RAW_HEADERS)},
            {"headers": {"authorization": "[REDACTED]", "accept": "text/plain"}},
            id="dict_with_bytes_keys_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"seen": {f"Authorization: Bearer {SECRET}": 1}},
            {"seen": {"Authorization: [REDACTED]": 1}},
            id="header_line_as_a_key_in_extra",
        ),
        pytest.param(
            "upstream headers %s",
            (_RAW_HEADERS,),
            {},
            {
                "message": (
                    "upstream headers [(b'authorization', b[REDACTED]),"
                    " (b'accept', b'application/json'), (b'accept', b'text/plain')]"
                ),
            },
            id="raw_headers_in_message",
        ),
        pytest.param(
            "upstream headers %s",
            ([("authorization", f"Bearer {SECRET}"), ("accept", "application/json")],),
            {},
            {
                "message": (
                    "upstream headers [('authorization', [REDACTED]),"
                    " ('accept', 'application/json')]"
                ),
            },
            id="header_items_in_message",
        ),
        pytest.param(
            "request scope %s",
            ({"type": "http", "headers": [(b"x-payment", SECRET.encode())]},),
            {},
            {"message": "request scope {'type': 'http', 'headers': [(b'x-payment', b[REDACTED])]}"},
            id="asgi_scope_in_message",
        ),
        pytest.param(
            "request headers %r",
            (Headers(raw=_RAW_HEADERS),),
            {},
            {
                "message": (
                    "request headers Headers(raw=[(b'authorization', b[REDACTED]),"
                    " (b'accept', b'application/json'), (b'accept', b'text/plain')])"
                ),
            },
            id="starlette_headers_with_a_repeated_name_in_message",
        ),
        pytest.param(
            "upstream headers %r",
            (httpx.Headers([("payment-signature", SECRET), ("payment-signature", SECRET)]),),
            {},
            {
                "message": (
                    "upstream headers Headers([('payment-signature', [REDACTED]),"
                    " ('payment-signature', [REDACTED])])"
                ),
            },
            id="httpx_headers_with_a_repeated_name_in_message",
        ),
        pytest.param(
            "payment %r",
            (_PaymentHeaders(payment_signature=SECRET, x_payment=SECRET),),
            {},
            {
                "message": (
                    "payment _PaymentHeaders(payment_signature=[REDACTED], x_payment=[REDACTED])"
                ),
            },
            id="header_named_fields_in_message",
        ),
        pytest.param(
            "forwarding",
            (),
            {"raw": f"Authorization: Bearer {SECRET}".encode()},
            {"raw": "Authorization: [REDACTED]"},
            id="bytes_in_extra",
        ),
        pytest.param(
            "settle failed",
            (),
            {"error": RuntimeError(f"facilitator rejected X-PAYMENT: {SECRET}")},
            {"error": "facilitator rejected X-PAYMENT: [REDACTED]"},
            id="exception_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"lines": {f"Authorization: Bearer {SECRET}"}},
            {"lines": "{'Authorization: [REDACTED]"},
            id="set_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"carrier": _HeaderCarrier()},
            {"carrier": "Cookie: [REDACTED]"},
            id="object_with_a_header_line_in_extra",
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
        pytest.param(
            "forwarding",
            (),
            {
                "headers": [
                    _HeaderPair("Authorization", f"Bearer {SECRET}"),
                    _HeaderPair("Accept", "application/json"),
                ]
            },
            {"headers": [["Authorization", "[REDACTED]"], ["Accept", "application/json"]]},
            id="namedtuple_pairs_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"headers": _SELF_REFERENCING_LIST},
            {},
            id="self_referencing_list_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"headers": _SELF_REFERENCING_DICT},
            {},
            id="self_referencing_dict_in_extra",
        ),
        pytest.param(
            "forwarding",
            (),
            {"broken": _ExplodingMapping()},
            {"broken": "[REDACTED]"},
            id="exploding_mapping_in_extra",
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

    (line,) = capsys.readouterr().out.splitlines()
    assert SECRET not in line
    assert expected.items() <= json.loads(line).items()


@pytest.mark.parametrize(
    "message",
    [
        "authorization, cookie and x-payment headers are redacted",
        "authorization failed for account 5",
    ],
)
def test_configure_logging_keeps_prose_that_names_a_sensitive_header(
    app_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
    message: str,
) -> None:
    configure_logging("INFO")

    app_logger.getChild("tests").info(message)

    (entry,) = _json_lines(capsys)
    assert entry["message"] == message


def test_configure_logging_keeps_the_container_type_of_a_redacted_header_pair(
    app_logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_logging("INFO")

    app_logger.getChild("tests").info(
        "forwarding",
        extra={"headers": [["Authorization", SECRET], ("Cookie", SECRET)]},
    )

    (record,) = caplog.records
    assert vars(record)["headers"] == [["Authorization", "[REDACTED]"], ("Cookie", "[REDACTED]")]


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


@pytest.mark.parametrize(
    ("message", "args", "expected"),
    [
        pytest.param(
            "sent %s to %s", (f"Authorization: {SECRET}",), "sent %s to %s", id="too_few_args"
        ),
        pytest.param("sent %s", (_Unprintable(),), "sent %s", id="unprintable_arg"),
        pytest.param(_Unprintable(), (), "<unprintable _Unprintable>", id="unprintable_message"),
    ],
)
def test_configure_logging_keeps_a_record_whose_message_does_not_render(
    app_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
    message: object,
    args: tuple[object, ...],
    expected: str,
) -> None:
    configure_logging("INFO")

    app_logger.getChild("tests").info(message, *args)

    (entry,) = _json_lines(capsys)
    assert entry["message"] == expected


def test_configure_logging_emits_the_stack_redacted(
    app_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    logger = app_logger.getChild("tests")
    # The stack shows each frame's source line, and a source line can quote a header.
    stack = f'Stack (most recent call last):\n  File "pay.py", line 1\n    X-PAYMENT: {SECRET}'

    logger.handle(logger.makeRecord(logger.name, logging.INFO, "pay.py", 1, "paying", (), None))
    logger.handle(
        logger.makeRecord(logger.name, logging.INFO, "pay.py", 1, "paying", (), None, sinfo=stack)
    )

    without_stack, with_stack = _json_lines(capsys)
    assert "stack" not in without_stack
    assert with_stack["stack"] == (
        'Stack (most recent call last):\n  File "pay.py", line 1\n    X-PAYMENT: [REDACTED]'
    )


def test_json_formatter_keeps_a_record_whose_fields_do_not_serialize() -> None:
    # Without RedactionFilter nothing turns these bytes keys into strings.
    record = logging.makeLogRecord(
        {"name": "app.tests", "levelname": "INFO", "msg": "forwarding", "headers": {b"a": 1}},
    )

    entry = json.loads(JsonFormatter().format(record))

    assert datetime.fromisoformat(str(entry.pop("time"))).tzinfo is not None
    assert entry == {
        "level": "INFO",
        "logger": "app.tests",
        "message": "forwarding",
        "log_error": "TypeError",
    }
