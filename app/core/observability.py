from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import FastAPI, Request, Response, status

from app.core.logging import (
    REQUEST_ID_HEADER,
    bind_request_id,
    build_log_context,
    get_logger,
    reset_request_id,
    resolve_request_id,
)
from app.core.problems import ProblemResponse

RequestHandler = Callable[[Request], Awaitable[Response]]
logger = get_logger(__name__)


def install_observability(app: FastAPI) -> None:
    """Install the request id, request logging and the catch-all 500.

    Install it last, so it runs outermost and also covers the other middleware.
    """

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: RequestHandler,
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = bind_request_id(request_id)
        start_time = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra=build_log_context(
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                ),
            )
            # The exception text may hold internals, so it is logged above and never returned.
            return ProblemResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                problem_type="internal_error",
                detail="an unexpected error occurred",
                headers={REQUEST_ID_HEADER: request_id},
            )
        else:
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
