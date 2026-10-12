"""The id that links a request's response header to its log lines."""

import re
from contextvars import ContextVar
from typing import Final
from uuid import uuid4

REQUEST_ID_HEADER: Final[str] = "X-Request-ID"
REQUEST_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._:-]{1,128}")

# Set by the request middleware and read by the log formatter.
current_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def resolve_request_id(request_id: str | None) -> str:
    """Return the client's X-Request-ID if it is safe to log and echo, else a new UUID."""
    if request_id is not None and REQUEST_ID_PATTERN.fullmatch(request_id):
        return request_id
    return str(uuid4())
