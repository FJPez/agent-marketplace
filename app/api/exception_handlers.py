from __future__ import annotations

import math

from asyncpg import LockNotAvailableError, QueryCanceledError
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from sqlalchemy.exc import DBAPIError

# Starlette's base class, so the handler also covers FastAPI's subclass and the router's 404/405.
from starlette.exceptions import HTTPException

from app.core.errors import ApplicationError
from app.core.logging import ERROR_CODE_FIELD, get_logger
from app.core.problems import ProblemResponse

logger = get_logger(__name__)


async def handle_application_error(request: Request, exc: ApplicationError) -> Response:
    return ProblemResponse(
        status_code=exc.status_code,
        problem_type=exc.problem_type,
        detail=str(exc),
        headers=exc.headers,
        extensions=exc.extensions,
    )


async def handle_http_exception(request: Request, exc: HTTPException) -> Response:
    return ProblemResponse(
        status_code=exc.status_code,
        detail=exc.detail,
        headers=exc.headers,
    )


async def handle_request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    # A malformed body can leave raw bytes or NaN in an error's `input`.
    errors = jsonable_encoder(
        exc.errors(),
        custom_encoder={
            bytes: lambda value: value.decode("utf-8", "replace"),
            float: lambda value: value if math.isfinite(value) else str(value),
        },
    )
    return ProblemResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        problem_type="invalid_input",
        detail="request validation failed; see errors",
        extensions={"errors": errors},
    )


async def handle_database_error(request: Request, exc: DBAPIError) -> Response:
    driver_error = exc.orig.__cause__ if exc.orig else None
    if not isinstance(driver_error, QueryCanceledError | LockNotAvailableError):
        # Not a statement or lock timeout, so it is left to the catch-all 500.
        raise exc
    logger.warning("database timeout", extra={ERROR_CODE_FIELD: driver_error.sqlstate})
    return ProblemResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        problem_type="unavailable",
        detail="the database did not answer in time; retry shortly",
        headers={"Retry-After": "1"},
    )


def install_exception_handlers(app: FastAPI) -> None:
    # `add_exception_handler` only type-checks handlers that take a bare `Exception`.
    app.exception_handler(ApplicationError)(handle_application_error)
    app.exception_handler(HTTPException)(handle_http_exception)
    app.exception_handler(RequestValidationError)(handle_request_validation_error)
    app.exception_handler(DBAPIError)(handle_database_error)
