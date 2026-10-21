import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Final

from app.core.request_id import current_request_id

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
        request_id = current_request_id.get()
        if request_id is not None:
            entry["request_id"] = request_id
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
