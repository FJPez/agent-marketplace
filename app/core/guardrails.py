from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response, status

from app.core.errors import UnauthenticatedError
from app.core.lifespan import get_resources
from app.core.problems import ProblemResponse
from app.core.resources import Resources
from app.services.auth import resolve_actor

RequestHandler = Callable[[Request], Awaitable[Response]]
_V1_PATH_PREFIX = "/v1/"
_GLOBAL_SCOPE = "global"


def build_client_rate_limit_key(request: Request) -> str:
    client_host = request.client.host if request.client is not None else "unknown"
    return f"client:{client_host}"


async def protect(request: Request, call_next: RequestHandler) -> Response:
    """Apply the global rate limit to /v1 requests, keyed by account or client address."""
    if not request.url.path.startswith(_V1_PATH_PREFIX):
        return await call_next(request)
    resources = get_resources(request.app)
    api_rate_limit = resources.settings.api_rate_limit
    backend = resources.rate_limits_backend
    key = await _resolve_owner_key(request, resources)
    if await backend.hit(api_rate_limit, key=key, scope=_GLOBAL_SCOPE):
        return await call_next(request)
    retry_after = await backend.seconds_until_reset(api_rate_limit, key=key, scope=_GLOBAL_SCOPE)
    return ProblemResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        problem_type="rate_limited",
        detail="rate limit exceeded",
        headers={"Retry-After": str(retry_after)},
    )


async def _resolve_owner_key(request: Request, resources: Resources) -> str:
    authorization = request.headers.get("Authorization")
    if authorization is None:
        return build_client_rate_limit_key(request)

    async with resources.db_session_factory() as session:
        try:
            actor = await resolve_actor(
                session=session,
                settings=resources.settings,
                authorization=authorization,
                touch_api_key=False,
            )
        except UnauthenticatedError:
            return build_client_rate_limit_key(request)
    return f"account:{actor.account_id}"


def install_guardrails(app: FastAPI) -> None:
    app.middleware("http")(protect)
