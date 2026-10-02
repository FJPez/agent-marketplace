from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.errors import UnauthenticatedError
from app.core.guardrails import protect
from app.core.resources import Resources, open_resources

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

API_RATE_LIMIT = "1/minute"


class _FakeRateLimitsBackend:
    def __init__(self, *, allow: bool, seconds_until_reset: int = 30) -> None:
        self._allow = allow
        self._seconds_until_reset = seconds_until_reset
        self.hits: list[tuple[str, str, str]] = []
        self.reset_lookups: list[tuple[str, str, str]] = []

    async def hit(self, limit_value: str, *, key: str, scope: str) -> bool:
        self.hits.append((limit_value, key, scope))
        return self._allow

    async def seconds_until_reset(self, limit_value: str, *, key: str, scope: str) -> int:
        self.reset_lookups.append((limit_value, key, scope))
        return self._seconds_until_reset


@pytest.fixture
async def resources() -> AsyncIterator[Resources]:
    # Nothing connects: resolve_actor is replaced wherever a session would be used.
    async with open_resources(Settings(api_rate_limit=API_RATE_LIMIT)) as opened:
        yield opened


def _build_request(
    resources: Resources,
    backend: _FakeRateLimitsBackend,
    *,
    path: str = "/v1/services",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    app = FastAPI()
    app.state.resources = replace(resources, rate_limits_backend=backend)
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "app": app,
    }
    return Request(scope, receive)


async def _respond_ok(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def test_protect_rejects_v1_requests_over_the_global_limit(resources: Resources) -> None:
    backend = _FakeRateLimitsBackend(allow=False, seconds_until_reset=42)

    response = await protect(_build_request(resources, backend), _respond_ok)

    assert response.status_code == 429
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["retry-after"] == "42"
    assert json.loads(bytes(response.body)) == {
        "type": "/problems/rate_limited",
        "status": 429,
        "detail": "rate limit exceeded",
    }
    assert backend.hits == [(API_RATE_LIMIT, "client:127.0.0.1", "global")]
    assert backend.reset_lookups == backend.hits


async def test_protect_passes_v1_requests_under_the_global_limit(resources: Resources) -> None:
    backend = _FakeRateLimitsBackend(allow=True)

    response = await protect(_build_request(resources, backend), _respond_ok)

    assert response.status_code == 200


async def test_protect_ignores_requests_outside_v1(resources: Resources) -> None:
    backend = _FakeRateLimitsBackend(allow=False)

    response = await protect(_build_request(resources, backend, path="/health"), _respond_ok)

    assert response.status_code == 200
    assert backend.hits == []


async def test_protect_uses_validated_actor_context_as_the_rate_limit_key(
    monkeypatch: pytest.MonkeyPatch,
    resources: Resources,
) -> None:
    backend = _FakeRateLimitsBackend(allow=True)
    request = _build_request(resources, backend, headers=[(b"authorization", b"Bearer token")])

    async def fake_resolve_actor(
        *, session: object, settings: object, authorization: str, touch_api_key: bool = True
    ) -> ActorContext:
        _ = session, settings, authorization, touch_api_key
        return ActorContext(account_id=42, wallet_address="0x1")

    monkeypatch.setattr("app.core.guardrails.resolve_actor", fake_resolve_actor)

    response = await protect(request, _respond_ok)

    assert response.status_code == 200
    assert backend.hits == [(API_RATE_LIMIT, "account:42", "global")]


async def test_protect_falls_back_to_client_key_for_unresolved_bearer_tokens(
    monkeypatch: pytest.MonkeyPatch,
    resources: Resources,
) -> None:
    backend = _FakeRateLimitsBackend(allow=True)
    request = _build_request(
        resources,
        backend,
        headers=[(b"authorization", b"Bearer stale-token")],
    )

    async def fake_resolve_actor(
        *, session: object, settings: object, authorization: str, touch_api_key: bool = True
    ) -> object:
        _ = session, settings, authorization, touch_api_key
        raise UnauthenticatedError("invalid access token")

    monkeypatch.setattr("app.core.guardrails.resolve_actor", fake_resolve_actor)

    response = await protect(request, _respond_ok)

    assert response.status_code == 200
    assert backend.hits == [(API_RATE_LIMIT, "client:127.0.0.1", "global")]
