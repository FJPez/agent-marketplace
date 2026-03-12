import logging
from typing import Final
from uuid import uuid4

REQUEST_ID_HEADER: Final[str] = "X-Request-ID"
REQUEST_ID_FIELD: Final[str] = "request_id"
METHOD_FIELD: Final[str] = "method"
PATH_FIELD: Final[str] = "path"
STATUS_CODE_FIELD: Final[str] = "status_code"
DURATION_MS_FIELD: Final[str] = "duration_ms"
ACCOUNT_ID_FIELD: Final[str] = "account_id"
SERVICE_ID_FIELD: Final[str] = "service_id"
QUOTE_ID_FIELD: Final[str] = "quote_id"
INVOCATION_ID_FIELD: Final[str] = "invocation_id"
ERROR_CODE_FIELD: Final[str] = "error_code"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def resolve_request_id(request_id: str | None) -> str:
    if request_id is not None:
        normalized_request_id = request_id.strip()
        if normalized_request_id:
            return normalized_request_id
    return str(uuid4())


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
