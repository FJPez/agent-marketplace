import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

REQUEST_ID_HEADER: Final[str] = "X-Request-ID"
REQUEST_ID_FIELD: Final[str] = "request_id"
METHOD_FIELD: Final[str] = "method"
PATH_FIELD: Final[str] = "path"
STATUS_CODE_FIELD: Final[str] = "status_code"
DURATION_MS_FIELD: Final[str] = "duration_ms"
ERROR_CODE_FIELD: Final[str] = "error_code"

_request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
REQUEST_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._:-]{1,128}")

REDACTED: Final[str] = "[REDACTED]"
# Header values that are credentials or spendable payment authorizations.
HEADER_NAME: Final[str] = r"authorization|cookie|payment[-_]signature|x[-_]payment"
# name: value   name=value   'name': 'value'
KEY_SEPARATOR: Final[str] = r"""['"]?\s*[:=]\s*"""
# ('name', 'value')
PAIR_SEPARATOR: Final[str] = r"""['"]\s*,\s*"""

SENSITIVE_HEADER_NAME: Final[re.Pattern[str]] = re.compile(HEADER_NAME, re.IGNORECASE)
SENSITIVE_HEADER_IN_TEXT: Final[re.Pattern[str]] = re.compile(
    rf"\b({HEADER_NAME})\b({KEY_SEPARATOR}|{PAIR_SEPARATOR}).*",
    re.IGNORECASE,
)
# Attributes every LogRecord has; any other attribute came from `extra`.
RECORD_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {*vars(logging.makeLogRecord({})), "message", "asctime"}
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(level: str) -> None:
    """Write `app.*` records at `level` and above to stdout as redacted JSON lines."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    app_logger = logging.getLogger("app")
    # Replaced, not added: create_app configures logging every time it builds an app.
    app_logger.handlers = [handler]
    app_logger.setLevel(level)


def redact(text: str) -> str:
    """Replace everything after a sensitive header name, to the end of its line."""
    return SENSITIVE_HEADER_IN_TEXT.sub(rf"\1\2{REDACTED}", text)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with sensitive header values redacted."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        request_id = get_request_id()
        if request_id is not None:
            entry[REQUEST_ID_FIELD] = request_id
        for name, value in vars(record).items():
            # An extra cannot replace a field already set above.
            if name in RECORD_ATTRIBUTES or name in entry:
                continue
            if SENSITIVE_HEADER_NAME.fullmatch(name):
                entry[name] = REDACTED
            elif value is None or isinstance(value, bool | int | float):
                entry[name] = value
            else:
                entry[name] = redact(str(value))
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        if record.stack_info:
            entry["stack"] = redact(record.stack_info)
        return json.dumps(entry)


def resolve_request_id(request_id: str | None) -> str:
    """Return the client's X-Request-ID if it is safe to log and echo, else a new UUID."""
    if request_id is not None and REQUEST_ID_PATTERN.fullmatch(request_id):
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
