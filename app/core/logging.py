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
_REQUEST_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._:-]{1,128}")

_REDACTED: Final[str] = "[REDACTED]"
_MAX_REDACTION_DEPTH: Final[int] = 8
# Header values that are credentials or spendable payment authorizations.
_SENSITIVE_HEADERS: Final[frozenset[str]] = frozenset(
    {"authorization", "cookie", "payment-signature", "x-payment"}
)
_SENSITIVE_HEADER_IN_TEXT: Final[re.Pattern[str]] = re.compile(
    rf"""
    # the header name, with "-" or "_" between its words
    \b({"|".join(name.replace("-", "[-_]") for name in sorted(_SENSITIVE_HEADERS))})\b
    (  # the separator before the value:
        ['"]\s*,\s*b?(?=['"])  # a quoted pair: ('name', 'value') or (b'name', b'value')
        |['"]?\s*[:=]\s*  # ":" or "=", possibly after the closing quote of a dict key
    )
    ('[^']*'|"[^"]*"|[^\r\n]*)  # a quoted value, else the rest of the line
    """,
    re.IGNORECASE | re.VERBOSE,
)
# Attributes every LogRecord has; any other attribute came from `extra`.
_RECORD_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {*vars(logging.makeLogRecord({})), "message", "asctime"}
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(level: str) -> None:
    """Write `app.*` records at `level` and above to stdout as redacted JSON lines.

    Only the `app` logger is configured, so uvicorn and third-party loggers keep their
    own settings, and records still propagate to the root logger, which has no handlers
    in production (pytest's caplog listens there). Calling it again replaces the handler
    instead of adding one.

    uvicorn's `uvicorn.error` logger, which prints the traceback of any exception that
    escapes the app, gets the redaction filter too (and keeps its own format).
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonFormatter())
    app_logger = logging.getLogger("app")
    app_logger.handlers = [handler]
    app_logger.setLevel(level)
    # `addFilter` ignores a filter the logger already has, so this never stacks.
    logging.getLogger("uvicorn.error").addFilter(_UVICORN_ERROR_FILTER)


def _redact_text(text: str) -> str:
    return _SENSITIVE_HEADER_IN_TEXT.sub(rf"\1\2{_REDACTED}", text)


def _as_text(value: object) -> str:
    # latin-1 maps every byte value to a character, so decoding cannot raise.
    return value.decode("latin-1") if isinstance(value, bytes) else str(value)


def _is_sensitive_header_name(name: object) -> bool:
    """True if `name` is a sensitive header, as a str or bytes (decoded latin-1).

    `-` and `_` are treated alike so `payment_signature` matches `PAYMENT-SIGNATURE`.
    """
    if not isinstance(name, (str, bytes)):
        return False
    return _as_text(name).lower().replace("_", "-") in _SENSITIVE_HEADERS


def _redact(value: object, depth: int = 0) -> object:
    """Return `value` as JSON-ready data with every sensitive header value redacted.

    None, booleans and numbers pass through. Text (bytes decoded as latin-1) is redacted
    by pattern. A mapping becomes a dict with text keys whose sensitive keys' values are
    redacted, and a list or tuple is redacted item by item. Anything else is redacted as
    its `str()`. A container nested deeper than `_MAX_REDACTION_DEPTH` becomes the
    redaction marker instead of being descended into. This is what ends a
    self-referencing list or dict (`a = []; a.append(a)`) without a `RecursionError`,
    with no need to track object identities.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (str, bytes)):
        return _redact_text(_as_text(value))
    if depth > _MAX_REDACTION_DEPTH:
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            _redact_text(_as_text(key)): (
                _REDACTED if _is_sensitive_header_name(key) else _redact(item, depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        # Rebuilt as a plain `list`/`tuple`, never `type(value)(...)`: a `tuple` subclass
        # (for example a namedtuple pair) usually cannot be constructed from one iterable.
        items = [_redact_sequence_item(item, depth + 1) for item in value]
        return items if isinstance(value, list) else tuple(items)
    return _redact_text(str(value))


def _redact_sequence_item(item: object, depth: int) -> object:
    # A `(name, value)` pair, as in `list(headers.items())` or `Headers.raw`.
    if isinstance(item, (list, tuple)) and len(item) == 2 and _is_sensitive_header_name(item[0]):
        pair = [_redact(item[0], depth), _REDACTED]
        return pair if isinstance(item, list) else tuple(pair)
    return _redact(item, depth)


def _render_message(record: logging.LogRecord) -> str:
    """The record's message, or its format string alone if the arguments do not render.

    Filters run outside logging's error handling, so this must not raise into the caller.
    """
    try:
        return record.getMessage()
    except Exception:
        try:
            return str(record.msg)
        except Exception:
            return f"<unprintable {type(record.msg).__name__}>"


class RedactionFilter(logging.Filter):
    """Remove Authorization, Cookie, PAYMENT-SIGNATURE and X-PAYMENT values from a record.

    Covers the rendered message, every `extra` value (an `extra` field named after a
    sensitive header, header mappings by key, `(name, value)` pairs in a list or tuple,
    and any other value by pattern on its text), the exception text and the stack. The
    record is edited in place, so every handler after this one, including the root
    logger's, sees only the redacted record, and every `extra` value is left JSON-ready.
    Never raises into the logging call: a message whose arguments do not render is
    logged as its format string, and an `extra` value that cannot be redacted (for
    example a `Mapping` whose `items()` raises) becomes the redaction marker.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_text(_render_message(record))
        record.args = ()
        for key in vars(record).keys() - _RECORD_ATTRIBUTES:
            if _is_sensitive_header_name(key):
                setattr(record, key, _REDACTED)
                continue
            try:
                redacted_value = _redact(getattr(record, key))
            except Exception:
                redacted_value = _REDACTED
            setattr(record, key, redacted_value)
        if record.exc_info and not record.exc_text:
            # logging.Formatter reuses exc_text, so the traceback is rendered once, here.
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = _redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = _redact_text(record.stack_info)
        return True


class _UvicornErrorFilter(RedactionFilter):
    """RedactionFilter for uvicorn's `uvicorn.error` logger.

    In a terminal, uvicorn's formatter prints the `color_message` extra in place of the
    message, rendered with the record's arguments. RedactionFilter consumes the
    arguments, so `color_message` is rendered here first (and then redacted like any
    other extra).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        fields = vars(record)
        color_message = fields.get("color_message")
        if isinstance(color_message, str) and record.args:
            try:
                fields["color_message"] = color_message % record.args
            except Exception:
                # uvicorn then prints the plain message, which always renders.
                del fields["color_message"]
        return super().filter(record)


_UVICORN_ERROR_FILTER: Final[RedactionFilter] = _UvicornErrorFilter()


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, request id, extras.

    Pair it with RedactionFilter, which renders the traceback into `exc_text`.
    """

    def format(self, record: logging.LogRecord) -> str:
        standard_fields: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry = dict(standard_fields)
        request_id = get_request_id()
        if request_id is not None:
            entry[REQUEST_ID_FIELD] = request_id
        for key, value in vars(record).items():
            # The standard output members above win: an extra field cannot rename
            # itself "level", "logger", "time" or "message" and overwrite them.
            if key in _RECORD_ATTRIBUTES or key in entry:
                continue
            entry[key] = value
        if record.exc_text:
            entry["exception"] = record.exc_text
        if record.stack_info:
            entry["stack"] = record.stack_info
        try:
            return json.dumps(entry, default=str)
        except Exception as exc:
            # A field that does not serialize (only possible without RedactionFilter,
            # which leaves every extra JSON-ready) must not lose the whole record.
            return json.dumps(standard_fields | {"log_error": type(exc).__name__})


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
