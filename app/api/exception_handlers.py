from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import NamedTuple

from fastapi import FastAPI, Request, status
from fastapi.responses import Response

from app.core.errors import (
    ApplicationError,
    ConflictError,
    InvalidInputError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
    UpstreamError,
    UpstreamTimeoutError,
)
from app.core.problems import problem_response
from app.core.request_schema_validation import PayloadSchemaMismatchError
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
    PayloadSchemaMismatchError: ProblemMapping(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_input"
    ),
    InvalidInputError: ProblemMapping(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_input"),
    UpstreamError: ProblemMapping(status.HTTP_502_BAD_GATEWAY, "upstream_error"),
    ReadinessCheckError: ProblemMapping(status.HTTP_503_SERVICE_UNAVAILABLE, "not_ready"),
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
