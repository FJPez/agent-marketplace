from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import UnauthenticatedError
from app.core.rate_limits_backend import (
    RateLimitsBackend,
    build_client_rate_limit_key,
    get_rate_limits_backend,
)
from app.services.auth import resolve_actor

RequestHandler = Callable[[Request], Awaitable[Response]]
_V1_PATH_PREFIX = "/v1/"


@dataclass(slots=True)
class ApiGuardrails:
    api_rate_limit: str
    rate_limits_backend: RateLimitsBackend = field(default_factory=get_rate_limits_backend)

    async def protect(
        self,
        request: Request,
        call_next: RequestHandler,
    ) -> Response:
        if request.url.path.startswith(_V1_PATH_PREFIX) and await self._is_rate_limited(request):
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        return await call_next(request)

    async def _is_rate_limited(self, request: Request) -> bool:
        return not await self.rate_limits_backend.hit(
            self.api_rate_limit,
            key=await self._resolve_owner_key(request),
            scope="global",
        )

    async def _resolve_owner_key(self, request: Request) -> str:
        authorization = request.headers.get("Authorization")
        app_state = getattr(request.app.state, "app_state", None)
        session_factory = getattr(app_state, "db_session_factory", None)
        if authorization is None or session_factory is None:
            return build_client_rate_limit_key(request)

        async with session_factory() as session:
            try:
                actor = await resolve_actor(
                    session=session,
                    settings=get_settings(),
                    authorization=authorization,
                    touch_api_key=False,
                )
            except UnauthenticatedError:
                return build_client_rate_limit_key(request)
        return f"account:{actor.account_id}"


def install_guardrails(app: FastAPI, *, guardrails: ApiGuardrails) -> None:
    @app.middleware("http")
    async def api_guardrails_middleware(
        request: Request,
        call_next: RequestHandler,
    ) -> Response:
        return await guardrails.protect(request, call_next)
