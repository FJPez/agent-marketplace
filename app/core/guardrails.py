from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.invoke_submission_backend import (
    InvokeSubmissionBackend,
    MemoryInvokeSubmissionBackend,
    SubmissionAcquireResult,
)
from app.core.rate_limits_backend import (
    RateLimitsBackend,
    build_actor_rate_limit_key,
    get_rate_limits_backend,
)
from app.services.auth_resolution_service import AuthResolutionError, AuthResolutionService

RequestHandler = Callable[[Request], Awaitable[Response]]
_INVOKE_PATH_PREFIX = "/v1/invoke/"
_QUOTE_PATH_SUFFIX = "/quote"
_V1_PATH_PREFIX = "/v1/"
_IDEMPOTENCY_HEADER = "Idempotency-Key"
RouteLimitScope = Literal["global", "invoke", "quote"]


@dataclass(slots=True)
class BufferedInvokeBody:
    body: bytes | None
    request_fingerprint: str | None
    payload_too_large: bool


@dataclass(slots=True)
class InvokeGuardrails:
    api_rate_limit: str
    invoke_rate_limit: str
    quote_rate_limit: str
    payload_max_bytes: int
    rate_limits_backend: RateLimitsBackend = field(default_factory=get_rate_limits_backend)
    invoke_submission_backend: InvokeSubmissionBackend = field(
        default_factory=MemoryInvokeSubmissionBackend
    )

    def applies_to(self, request: Request) -> bool:
        return request.url.path.startswith(_V1_PATH_PREFIX) or self._is_invoke_request(request)

    async def protect(
        self,
        request: Request,
        call_next: RequestHandler,
    ) -> Response:
        if not self.applies_to(request):
            return await call_next(request)

        if not self._is_invoke_request(request):
            if self._has_global_policy(request) and await self._is_globally_rate_limited(request):
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})

            route_limit = self._route_specific_limit(request)
            if route_limit is not None and await self._is_route_rate_limited(request, route_limit):
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
            return await call_next(request)

        buffered_body = await self._read_invoke_body(request)
        if buffered_body.payload_too_large:
            return JSONResponse(
                status_code=413,
                content={"detail": "request payload too large"},
            )

        owner_key = await self._resolve_owner_key(request)
        submission_key, request_fingerprint = self._build_submission_key(
            request,
            owner_key=owner_key,
            request_fingerprint=buffered_body.request_fingerprint,
        )

        if submission_key is not None:
            acquire_result = await self.invoke_submission_backend.acquire(
                submission_key,
                request_fingerprint,
            )
            if acquire_result is SubmissionAcquireResult.REQUEST_IN_PROGRESS:
                return JSONResponse(
                    status_code=409,
                    content={"detail": "request already in progress"},
                )
            if acquire_result is SubmissionAcquireResult.IDEMPOTENCY_KEY_REUSED:
                return JSONResponse(
                    status_code=409,
                    content={"detail": "idempotency key already used for a different request"},
                )

        try:
            if self._has_global_policy(request) and await self._is_globally_rate_limited(request):
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})

            route_limit = self._route_specific_limit(request)
            if route_limit is not None and await self._is_route_rate_limited(request, route_limit):
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})

            return await call_next(request)
        finally:
            if submission_key is not None:
                await self.invoke_submission_backend.release(
                    submission_key,
                    request_fingerprint,
                )

    async def reset_rate_limits(self) -> None:
        await self.rate_limits_backend.reset()

    async def _read_invoke_body(self, request: Request) -> BufferedInvokeBody:
        declared_content_length = request.headers.get("content-length")
        if declared_content_length is not None:
            try:
                if int(declared_content_length) > self.payload_max_bytes:
                    return BufferedInvokeBody(
                        body=None,
                        request_fingerprint=None,
                        payload_too_large=True,
                    )
            except ValueError:
                pass

        digest = sha256()
        chunks: list[bytes] = []
        total_size = 0
        async for chunk in request.stream():
            total_size += len(chunk)
            if total_size > self.payload_max_bytes:
                return BufferedInvokeBody(
                    body=None,
                    request_fingerprint=None,
                    payload_too_large=True,
                )
            chunks.append(chunk)
            digest.update(chunk)

        body = b"".join(chunks)
        request._body = body
        return BufferedInvokeBody(
            body=body,
            request_fingerprint=digest.hexdigest(),
            payload_too_large=False,
        )

    def _build_submission_key(
        self,
        request: Request,
        *,
        owner_key: str,
        request_fingerprint: str | None,
    ) -> tuple[str | None, str]:
        if request_fingerprint is None:
            request_fingerprint = sha256(b"").hexdigest()
        idempotency_key = request.headers.get(_IDEMPOTENCY_HEADER)
        if not idempotency_key:
            return None, request_fingerprint

        submission_key = ":".join((owner_key, request.url.path, idempotency_key))
        return submission_key, request_fingerprint

    def _has_global_policy(self, request: Request) -> bool:
        return request.url.path.startswith(_V1_PATH_PREFIX)

    def _is_invoke_request(self, request: Request) -> bool:
        return request.method.upper() == "POST" and request.url.path.startswith(_INVOKE_PATH_PREFIX)

    def _is_quote_request(self, request: Request) -> bool:
        return (
            request.method.upper() == "POST"
            and request.url.path.startswith("/v1/services/")
            and request.url.path.endswith(_QUOTE_PATH_SUFFIX)
        )

    def _route_specific_limit(self, request: Request) -> tuple[RouteLimitScope, str] | None:
        if self._is_invoke_request(request):
            return "invoke", self.invoke_rate_limit
        if self._is_quote_request(request):
            return "quote", self.quote_rate_limit
        return None

    async def _is_route_rate_limited(
        self,
        request: Request,
        route_limit: tuple[RouteLimitScope, str],
    ) -> bool:
        scope, limit_value = route_limit
        return not await self.rate_limits_backend.hit(
            limit_value,
            key=await self._resolve_owner_key(request),
            scope=scope,
        )

    async def _is_globally_rate_limited(self, request: Request) -> bool:
        return not await self.rate_limits_backend.hit(
            self.api_rate_limit,
            key=await self._resolve_owner_key(request),
            scope="global",
        )

    async def _resolve_owner_key(self, request: Request) -> str:
        cached_key = getattr(request.state, "rate_limit_owner_key", None)
        if isinstance(cached_key, str):
            return cached_key

        authorization = request.headers.get("Authorization")
        if authorization is None:
            owner_key = build_actor_rate_limit_key(request)
            request.state.rate_limit_owner_key = owner_key
            return owner_key

        app_state = getattr(request.app.state, "app_state", None)
        session_factory = getattr(app_state, "db_session_factory", None)
        if session_factory is None:
            owner_key = build_actor_rate_limit_key(request)
            request.state.rate_limit_owner_key = owner_key
            return owner_key

        async with session_factory() as session:
            service = AuthResolutionService(session, settings=get_settings())
            try:
                actor = await service.resolve_actor(
                    authorization=authorization,
                    touch_api_key=False,
                )
            except AuthResolutionError:
                owner_key = build_actor_rate_limit_key(request)
            else:
                owner_key = f"account:{actor.account_id}"

        request.state.rate_limit_owner_key = owner_key
        return owner_key


def install_guardrails(app: FastAPI, *, guardrails: InvokeGuardrails) -> None:
    @app.middleware("http")
    async def invoke_guardrails_middleware(
        request: Request,
        call_next: RequestHandler,
    ) -> Response:
        return await guardrails.protect(request, call_next)
