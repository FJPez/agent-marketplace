from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import Response

from app.core.errors import (
    ConflictError,
    InvalidInputError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from app.core.request_schema_validation import PayloadSchemaMismatchError
from app.services.health_service import ReadinessCheckError
from app.services.invoke_service import (
    InvokeBadGatewayError,
    InvokeConflictError,
    InvokeGatewayTimeoutError,
    InvokeNotFoundError,
    InvokeUnavailableError,
)
from app.services.moderation_service import (
    InvalidModerationTransitionError,
    ModeratedServiceNotFoundError,
)
from app.services.payout_service import PayoutConflictError
from app.services.quote_service import QuoteNotFoundError, QuoteUnavailableError

Handler = Callable[[Request, Exception], Awaitable[Response]]

# Starlette resolves handlers by walking the raised exception's MRO, so the
# app.core.errors base classes act as fallbacks for any unregistered subclass.
STATUS_CODES: dict[type[Exception], int] = {
    UnauthenticatedError: status.HTTP_401_UNAUTHORIZED,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    QuoteNotFoundError: status.HTTP_404_NOT_FOUND,
    InvokeNotFoundError: status.HTTP_404_NOT_FOUND,
    ModeratedServiceNotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidStateError: status.HTTP_409_CONFLICT,
    QuoteUnavailableError: status.HTTP_409_CONFLICT,
    InvokeConflictError: status.HTTP_409_CONFLICT,
    InvokeUnavailableError: status.HTTP_409_CONFLICT,
    InvalidModerationTransitionError: status.HTTP_409_CONFLICT,
    PayoutConflictError: status.HTTP_409_CONFLICT,
    PayloadSchemaMismatchError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvokeBadGatewayError: status.HTTP_502_BAD_GATEWAY,
    ReadinessCheckError: status.HTTP_503_SERVICE_UNAVAILABLE,
    InvokeGatewayTimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
}


def _build_handler(status_code: int) -> Handler:
    async def handle_exception(request: Request, exc: Exception) -> Response:
        http_exc = HTTPException(status_code=status_code, detail=str(exc))
        return await http_exception_handler(request, http_exc)

    return handle_exception


def install_exception_handlers(app: FastAPI) -> None:
    for exc_type, status_code in STATUS_CODES.items():
        app.add_exception_handler(exc_type, _build_handler(status_code))
