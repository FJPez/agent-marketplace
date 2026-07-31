from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import Response

from app.core.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from app.core.request_schema_validation import PayloadSchemaMismatchError
from app.services.api_key_service import ApiKeyNotFoundError, ApiKeyValidationError
from app.services.auth_resolution_service import AuthResolutionError, JwtAuthRequiredError
from app.services.auth_service import AuthenticationError
from app.services.discovery_service import DiscoveryNotFoundError
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
from app.services.provider_service_errors import (
    ProviderServiceConflictError,
    ProviderServiceNotFoundError,
    ProviderServiceStateError,
    ProviderServiceValidationError,
)
from app.services.quote_service import QuoteNotFoundError, QuoteUnavailableError
from app.services.wallet_change_service import WalletChangeError

Handler = Callable[[Request, Exception], Awaitable[Response]]

# Starlette resolves handlers by walking the raised exception's MRO, so the
# app.core.errors base classes act as fallbacks for any unregistered subclass.
STATUS_CODES: dict[type[Exception], int] = {
    UnauthenticatedError: status.HTTP_401_UNAUTHORIZED,
    AuthResolutionError: status.HTTP_401_UNAUTHORIZED,
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    JwtAuthRequiredError: status.HTTP_403_FORBIDDEN,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ApiKeyNotFoundError: status.HTTP_404_NOT_FOUND,
    DiscoveryNotFoundError: status.HTTP_404_NOT_FOUND,
    QuoteNotFoundError: status.HTTP_404_NOT_FOUND,
    InvokeNotFoundError: status.HTTP_404_NOT_FOUND,
    ProviderServiceNotFoundError: status.HTTP_404_NOT_FOUND,
    ModeratedServiceNotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidStateError: status.HTTP_409_CONFLICT,
    WalletChangeError: status.HTTP_409_CONFLICT,
    QuoteUnavailableError: status.HTTP_409_CONFLICT,
    InvokeConflictError: status.HTTP_409_CONFLICT,
    InvokeUnavailableError: status.HTTP_409_CONFLICT,
    ProviderServiceConflictError: status.HTTP_409_CONFLICT,
    ProviderServiceStateError: status.HTTP_409_CONFLICT,
    InvalidModerationTransitionError: status.HTTP_409_CONFLICT,
    PayoutConflictError: status.HTTP_409_CONFLICT,
    ApiKeyValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PayloadSchemaMismatchError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ProviderServiceValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvokeBadGatewayError: status.HTTP_502_BAD_GATEWAY,
    ReadinessCheckError: status.HTTP_503_SERVICE_UNAVAILABLE,
    InvokeGatewayTimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
}

# Exceptions whose response detail is a fixed string instead of str(exc), so
# internal state cannot leak for these resources.
REDACTED_DETAILS: dict[type[Exception], str] = {
    ApiKeyNotFoundError: "api key not found",
}


def _build_handler(status_code: int, fixed_detail: str | None) -> Handler:
    async def handle_exception(request: Request, exc: Exception) -> Response:
        http_exc = HTTPException(
            status_code=status_code,
            detail=fixed_detail if fixed_detail is not None else str(exc),
        )
        return await http_exception_handler(request, http_exc)

    return handle_exception


def install_exception_handlers(app: FastAPI) -> None:
    for exc_type, status_code in STATUS_CODES.items():
        handler = _build_handler(status_code, REDACTED_DETAILS.get(exc_type))
        app.add_exception_handler(exc_type, handler)
