import logging
from contextvars import ContextVar, Token
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
QUOTE_ID_FIELD: Final[str] = "quote_id"
INVOCATION_ID_FIELD: Final[str] = "invocation_id"
PAYMENT_ATTEMPT_ID_FIELD: Final[str] = "payment_attempt_id"
PAYOUT_ID_FIELD: Final[str] = "payout_id"
PAYOUT_STATUS_FIELD: Final[str] = "payout_status"
PAYOUT_COUNT_FIELD: Final[str] = "payout_count"
TRANSFER_REFERENCE_FIELD: Final[str] = "transfer_reference"
ERROR_CODE_FIELD: Final[str] = "error_code"

_request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def resolve_request_id(request_id: str | None) -> str:
    if request_id is not None:
        normalized_request_id = request_id.strip()
        if normalized_request_id:
            return normalized_request_id
    return str(uuid4())


def bind_request_id(request_id: str) -> Token[str | None]:
    return _request_id_context.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id_context.reset(token)


def get_request_id() -> str | None:
    request_id = _request_id_context.get()
    if request_id is None:
        return None
    normalized_request_id = request_id.strip()
    return normalized_request_id or None


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
