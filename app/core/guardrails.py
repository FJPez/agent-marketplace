from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.rate_limits_backend import (
    RateLimitsBackend,
    build_client_rate_limit_key,
    get_rate_limits_backend,
)

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
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _in_flight_requests: dict[str, str] = field(default_factory=dict)

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

        submission_key, request_fingerprint = self._build_submission_key(
            request,
            owner_key=build_client_rate_limit_key(request),
            request_fingerprint=buffered_body.request_fingerprint,
        )

        async with self._lock:
            if submission_key is not None:
                existing_fingerprint = self._in_flight_requests.get(submission_key)
                if existing_fingerprint is not None:
                    detail = "request already in progress"
                    if existing_fingerprint != request_fingerprint:
                        detail = "idempotency key already used for a different request"
                    return JSONResponse(status_code=409, content={"detail": detail})
                self._in_flight_requests[submission_key] = request_fingerprint

        try:
            if self._has_global_policy(request) and await self._is_globally_rate_limited(request):
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})

            route_limit = self._route_specific_limit(request)
            if route_limit is not None and await self._is_route_rate_limited(request, route_limit):
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})

            return await call_next(request)
        finally:
            if submission_key is not None:
                async with self._lock:
                    self._in_flight_requests.pop(submission_key, None)

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
            key=build_client_rate_limit_key(request),
            scope=scope,
        )

    async def _is_globally_rate_limited(self, request: Request) -> bool:
        return not await self.rate_limits_backend.hit(
            self.api_rate_limit,
            key=build_client_rate_limit_key(request),
            scope="global",
        )


def install_guardrails(app: FastAPI, *, guardrails: InvokeGuardrails) -> None:
    @app.middleware("http")
    async def invoke_guardrails_middleware(
        request: Request,
        call_next: RequestHandler,
    ) -> Response:
        return await guardrails.protect(request, call_next)
