from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import Response

from app.core.errors import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.request_schema_validation import PayloadSchemaMismatchError
from app.services.account_service import AccountNotFoundError, AccountValidationError
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

DetailBuilder = Callable[[Exception], str]
Handler = Callable[[Request, Exception], Awaitable[Response]]


@dataclass(frozen=True, slots=True)
class ExceptionHandlerConfig:
    status_code: int
    detail_builder: DetailBuilder


def _detail_from_exception(exc: Exception) -> str:
    return str(exc)


def _fixed_detail(detail: str) -> DetailBuilder:
    def build_detail(_: Exception) -> str:
        return detail

    return build_detail


def _build_handler(config: ExceptionHandlerConfig) -> Handler:
    async def handle_exception(request: Request, exc: Exception) -> Response:
        http_exc = HTTPException(
            status_code=config.status_code,
            detail=config.detail_builder(exc),
        )
        return await http_exception_handler(request, http_exc)

    return handle_exception


EXCEPTION_HANDLERS: tuple[tuple[type[Exception], ExceptionHandlerConfig], ...] = (
    (
        AuthResolutionError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        AuthenticationError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        JwtAuthRequiredError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_403_FORBIDDEN,
            detail_builder=_detail_from_exception,
        ),
    ),
    # base taxonomy fallback (app.core.errors)
    (
        PermissionDeniedError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_403_FORBIDDEN,
            detail_builder=_detail_from_exception,
        ),
    ),
    # base taxonomy fallback (app.core.errors)
    (
        NotFoundError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_404_NOT_FOUND,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        AccountNotFoundError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_404_NOT_FOUND,
            detail_builder=_fixed_detail("account not found"),
        ),
    ),
    (
        ApiKeyNotFoundError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_404_NOT_FOUND,
            detail_builder=_fixed_detail("api key not found"),
        ),
    ),
    (
        DiscoveryNotFoundError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_404_NOT_FOUND,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        QuoteNotFoundError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_404_NOT_FOUND,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        InvokeNotFoundError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_404_NOT_FOUND,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        ProviderServiceNotFoundError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_404_NOT_FOUND,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        ModeratedServiceNotFoundError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_404_NOT_FOUND,
            detail_builder=_detail_from_exception,
        ),
    ),
    # base taxonomy fallback (app.core.errors)
    (
        ConflictError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    # base taxonomy fallback (app.core.errors)
    (
        InvalidStateError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        WalletChangeError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        QuoteUnavailableError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        InvokeConflictError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        InvokeUnavailableError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        ProviderServiceConflictError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        ProviderServiceStateError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        InvalidModerationTransitionError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        PayoutConflictError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_409_CONFLICT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        AccountValidationError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        ApiKeyValidationError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        PayloadSchemaMismatchError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        ProviderServiceValidationError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        InvokeBadGatewayError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        ReadinessCheckError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail_builder=_detail_from_exception,
        ),
    ),
    (
        InvokeGatewayTimeoutError,
        ExceptionHandlerConfig(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail_builder=_detail_from_exception,
        ),
    ),
)


def install_exception_handlers(app: FastAPI) -> None:
    for exc_type, config in EXCEPTION_HANDLERS:
        app.add_exception_handler(exc_type, _build_handler(config))
