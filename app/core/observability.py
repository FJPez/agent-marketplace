from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from app.core.logging import (
    REQUEST_ID_HEADER,
    bind_request_id,
    build_log_context,
    get_logger,
    reset_request_id,
    resolve_request_id,
)

RequestHandler = Callable[[Request], Awaitable[Response]]
logger = get_logger(__name__)


def install_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: RequestHandler,
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        start_time = perf_counter()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request completed",
                extra=build_log_context(
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=int((perf_counter() - start_time) * 1000),
                ),
            )
            return response
        finally:
            reset_request_id(token)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
        request_id = getattr(request.state, "request_id", None)
        if not isinstance(request_id, str):
            request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))

        logger.exception(
            "request failed",
            exc_info=exc,
            extra=build_log_context(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
            ),
        )
        response = PlainTextResponse("Internal Server Error", status_code=500)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
