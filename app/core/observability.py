from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.core.logging import REQUEST_ID_HEADER, resolve_request_id

RequestHandler = Callable[[Request], Awaitable[Response]]


def install_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: RequestHandler,
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
