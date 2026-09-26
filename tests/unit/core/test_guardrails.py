from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from app.core.actor import ActorContext
from app.core.errors import UnauthenticatedError
from app.core.guardrails import ApiGuardrails

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _FakeRateLimitsBackend:
    def __init__(self, *, allow: bool) -> None:
        self.allow = allow
        self.hits: list[tuple[str, str, str]] = []

    async def hit(self, limit_value: str, *, key: str, scope: str) -> bool:
        self.hits.append((limit_value, key, scope))
        return self.allow

    async def reset(self) -> None:
        self.hits.clear()


def _build_request(
    *,
    path: str = "/v1/services",
    headers: list[tuple[bytes, bytes]] | None = None,
    app: FastAPI | None = None,
) -> Request:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "app": app or FastAPI(),
    }
    return Request(scope, receive)


async def _respond_ok(request: Request) -> Response:
    _ = request
    return JSONResponse({"status": "ok"})


async def test_protect_rejects_v1_requests_over_the_global_limit() -> None:
    backend = _FakeRateLimitsBackend(allow=False)
    guardrails = ApiGuardrails(api_rate_limit="1/minute", rate_limits_backend=backend)

    response = await guardrails.protect(_build_request(), _respond_ok)

    assert response.status_code == 429
    assert response.body == b'{"detail":"rate limit exceeded"}'
    assert backend.hits == [("1/minute", "client:127.0.0.1", "global")]


async def test_protect_passes_v1_requests_under_the_global_limit() -> None:
    backend = _FakeRateLimitsBackend(allow=True)
    guardrails = ApiGuardrails(api_rate_limit="1/minute", rate_limits_backend=backend)

    response = await guardrails.protect(_build_request(), _respond_ok)

    assert response.status_code == 200


async def test_protect_ignores_requests_outside_v1() -> None:
    backend = _FakeRateLimitsBackend(allow=False)
    guardrails = ApiGuardrails(api_rate_limit="1/minute", rate_limits_backend=backend)

    response = await guardrails.protect(_build_request(path="/health"), _respond_ok)

    assert response.status_code == 200
    assert backend.hits == []


async def test_resolve_owner_key_uses_validated_actor_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def session_factory() -> AsyncIterator[object]:
        yield object()

    app = FastAPI()
    app.state.app_state = SimpleNamespace(db_session_factory=session_factory)
    guardrails = ApiGuardrails(
        api_rate_limit="10/minute",
        rate_limits_backend=_FakeRateLimitsBackend(allow=True),
    )
    request = _build_request(headers=[(b"authorization", b"Bearer token")], app=app)

    async def fake_resolve_actor(
        *, session: object, settings: object, authorization: str, touch_api_key: bool = True
    ) -> ActorContext:
        _ = session, settings, authorization, touch_api_key
        return ActorContext(account_id=42, wallet_address="0x1")

    monkeypatch.setattr("app.core.guardrails.resolve_actor", fake_resolve_actor)

    owner_key = await guardrails._resolve_owner_key(request)

    assert owner_key == "account:42"


async def test_resolve_owner_key_falls_back_to_client_key_for_unresolved_bearer_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def session_factory() -> AsyncIterator[object]:
        yield object()

    app = FastAPI()
    app.state.app_state = SimpleNamespace(db_session_factory=session_factory)
    guardrails = ApiGuardrails(
        api_rate_limit="10/minute",
        rate_limits_backend=_FakeRateLimitsBackend(allow=True),
    )
    request = _build_request(headers=[(b"authorization", b"Bearer stale-token")], app=app)

    async def fake_resolve_actor(
        *, session: object, settings: object, authorization: str, touch_api_key: bool = True
    ) -> object:
        _ = session, settings, authorization, touch_api_key
        raise UnauthenticatedError("invalid access token")

    monkeypatch.setattr("app.core.guardrails.resolve_actor", fake_resolve_actor)

    owner_key = await guardrails._resolve_owner_key(request)

    assert owner_key == "client:127.0.0.1"
