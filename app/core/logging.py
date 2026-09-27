import json
import logging
import re
import sys
from collections.abc import Mapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

REQUEST_ID_HEADER: Final[str] = "X-Request-ID"
REQUEST_ID_FIELD: Final[str] = "request_id"
EVENT_FIELD: Final[str] = "event"
METHOD_FIELD: Final[str] = "method"
PATH_FIELD: Final[str] = "path"
STATUS_CODE_FIELD: Final[str] = "status_code"
DURATION_MS_FIELD: Final[str] = "duration_ms"
ACCOUNT_ID_FIELD: Final[str] = "account_id"
PROVIDER_ACCOUNT_ID_FIELD: Final[str] = "provider_account_id"
SERVICE_ID_FIELD: Final[str] = "service_id"
ERROR_CODE_FIELD: Final[str] = "error_code"

_request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")

_REDACTED = "[REDACTED]"
# Header values that are credentials or spendable payment authorizations.
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "payment-signature", "x-payment"})
_SENSITIVE_HEADER_IN_TEXT = re.compile(
    rf"""
    \b({"|".join(sorted(_SENSITIVE_HEADERS))})\b  # the header name
    (['"]?\s*[:=]\s*)  # ":" or "=", possibly after the closing quote of a dict key
    ('[^']*'|"[^"]*"|[^\r\n]*)  # a quoted value, else the rest of the line
    """,
    re.IGNORECASE | re.VERBOSE,
)
# Attributes every LogRecord has; any other attribute came from `extra`.
_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(level: str) -> None:
    """Write `app.*` records at `level` and above to stdout as redacted JSON lines.

    Both entry points call this (`create_app` and the worker). Only the `app` logger is
    configured, so uvicorn and third-party loggers keep their own settings, and records
    still propagate to the root logger, which has no handlers in production (pytest's
    caplog listens there). Calling it again replaces the handler instead of adding one.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter())
    app_logger = logging.getLogger("app")
    app_logger.handlers = [handler]
    app_logger.setLevel(level)


def _redact_text(text: str) -> str:
    return _SENSITIVE_HEADER_IN_TEXT.sub(rf"\1\2{_REDACTED}", text)


def _redact(value: object) -> object:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        return {
            key: _REDACTED if str(key).lower() in _SENSITIVE_HEADERS else _redact(item)
            for key, item in value.items()
        }
    return value


class RedactionFilter(logging.Filter):
    """Remove Authorization, Cookie, PAYMENT-SIGNATURE and X-PAYMENT values from a record.

    Covers the rendered message, `extra` values (header mappings by key, strings by
    pattern) and the exception text. The record is edited in place, so every handler
    after this one, including the root logger's, sees only the redacted record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError, KeyError):
            # Filters run outside logging's error handling, so a call whose arguments
            # do not fit its format string would raise into the caller. Log the format
            # string alone instead.
            message = str(record.msg)
        record.msg = _redact_text(message)
        record.args = ()
        for key in vars(record).keys() - _RECORD_ATTRIBUTES:
            setattr(record, key, _redact(getattr(record, key)))
        if record.exc_info and not record.exc_text:
            # logging.Formatter reuses exc_text, so the traceback is rendered once, here.
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = _redact_text(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, request id, extras.

    Pair it with RedactionFilter, which renders the traceback into `exc_text`.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id is not None:
            entry[REQUEST_ID_FIELD] = request_id
        entry.update(
            (key, value) for key, value in vars(record).items() if key not in _RECORD_ATTRIBUTES
        )
        if record.exc_text:
            entry["exception"] = record.exc_text
        return json.dumps(entry, default=str)


def resolve_request_id(request_id: str | None) -> str:
    """Return the client's X-Request-ID if it is safe to log and echo, else a new UUID.

    A valid id is 1 to 128 ASCII letters, digits, ".", "_", ":" or "-" (UUIDs, hex trace
    ids and common proxy formats). Anything else is replaced, never trimmed or escaped.
    """
    if request_id is not None and _REQUEST_ID_PATTERN.fullmatch(request_id):
        return request_id
    return str(uuid4())


def bind_request_id(request_id: str) -> Token[str | None]:
    return _request_id_context.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id_context.reset(token)


def get_request_id() -> str | None:
    return _request_id_context.get()


def build_log_context(
    request_id: str,
    method: str,
    path: str,
    *,
    status_code: int | None = None,
    duration_ms: int | None = None,
) -> dict[str, str | int]:
    context: dict[str, str | int] = {
        REQUEST_ID_FIELD: request_id,
        METHOD_FIELD: method,
        PATH_FIELD: path,
    }
    if status_code is not None:
        context[STATUS_CODE_FIELD] = status_code
    if duration_ms is not None:
        context[DURATION_MS_FIELD] = duration_ms
    return context


def build_event_context(
    event: str,
    **fields: str | int | None,
) -> dict[str, str | int]:
    context: dict[str, str | int] = {EVENT_FIELD: event}
    request_id = get_request_id()
    if request_id is not None:
        context[REQUEST_ID_FIELD] = request_id
    for field_name, value in fields.items():
        if value is not None:
            context[field_name] = value
    return context
