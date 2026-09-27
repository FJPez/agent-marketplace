from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from typing import NamedTuple

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response

# Starlette's HTTPException, not FastAPI's subclass of it, so the handler below also
# catches FastAPI's own HTTPException and the router's own 404/405.
from starlette.exceptions import HTTPException

from app.core.errors import (
    ApplicationError,
    ConflictError,
    InvalidInputError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
    UnavailableError,
    UpstreamError,
    UpstreamTimeoutError,
)
from app.core.problems import problem_response
from app.services.health_service import ReadinessCheckError

Handler = Callable[[Request, Exception], Awaitable[Response]]


class ProblemMapping(NamedTuple):
    status_code: int
    problem_type: str


# Starlette resolves handlers by walking the raised exception's MRO, so the
# app.core.errors base classes act as fallbacks for any unregistered subclass.
PROBLEM_MAPPINGS: dict[type[Exception], ProblemMapping] = {
    UnauthenticatedError: ProblemMapping(status.HTTP_401_UNAUTHORIZED, "unauthenticated"),
    PermissionDeniedError: ProblemMapping(status.HTTP_403_FORBIDDEN, "permission_denied"),
    NotFoundError: ProblemMapping(status.HTTP_404_NOT_FOUND, "not_found"),
    ConflictError: ProblemMapping(status.HTTP_409_CONFLICT, "conflict"),
    InvalidStateError: ProblemMapping(status.HTTP_409_CONFLICT, "invalid_state"),
    InvalidInputError: ProblemMapping(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_input"),
    UpstreamError: ProblemMapping(status.HTTP_502_BAD_GATEWAY, "upstream_error"),
    ReadinessCheckError: ProblemMapping(status.HTTP_503_SERVICE_UNAVAILABLE, "not_ready"),
    UnavailableError: ProblemMapping(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable"),
    UpstreamTimeoutError: ProblemMapping(status.HTTP_504_GATEWAY_TIMEOUT, "upstream_timeout"),
}


def _build_handler(mapping: ProblemMapping) -> Handler:
    async def handle_exception(request: Request, exc: Exception) -> Response:
        if isinstance(exc, ApplicationError):
            return problem_response(
                status_code=mapping.status_code,
                problem_type=exc.problem_type or mapping.problem_type,
                detail=str(exc),
                headers=exc.headers,
                extensions=exc.extensions,
            )
        return problem_response(
            status_code=mapping.status_code,
            problem_type=mapping.problem_type,
            detail=str(exc),
        )

    return handle_exception


def install_exception_handlers(app: FastAPI) -> None:
    for exc_type, mapping in PROBLEM_MAPPINGS.items():
        app.add_exception_handler(exc_type, _build_handler(mapping))

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> Response:
        # Registered on Starlette's HTTPException base so this one handler also catches
        # FastAPI's HTTPException and the router's own 404/405; all of these carry no
        # meaning beyond their status code, so they render as about:blank.
        return problem_response(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> Response:
        # A malformed body can put raw non-UTF-8 bytes or a non-finite float (NaN,
        # Infinity) into an error's `input`; the default encoder can't render either
        # (`bytes.decode()` raises, and `JSONResponse` disallows non-finite floats), so
        # both are coerced into JSON-safe values here instead of crashing to a 500.
        errors = jsonable_encoder(
            exc.errors(),
            custom_encoder={
                bytes: lambda value: value.decode("utf-8", "replace"),
                float: lambda value: value if math.isfinite(value) else str(value),
            },
        )
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            problem_type="invalid_input",
            detail="request validation failed; see errors",
            extensions={"errors": errors},
        )
