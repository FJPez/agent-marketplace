from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.core.errors import UnauthenticatedError
from app.core.guardrails import InvokeGuardrails

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _build_streaming_request(
    *,
    chunks: list[bytes],
    headers: list[tuple[bytes, bytes]] | None = None,
    app: FastAPI | None = None,
) -> Request:
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    if not messages:
        messages = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/invoke/1",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "app": app or FastAPI(),
    }
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_read_invoke_body_rejects_payload_once_streamed_size_exceeds_limit() -> None:
    guardrails = InvokeGuardrails(
        api_rate_limit="10/minute",
        invoke_rate_limit="10/minute",
        quote_rate_limit="10/minute",
        payload_max_bytes=5,
    )
    request = _build_streaming_request(chunks=[b"abc", b"def"])

    result = await guardrails._read_invoke_body(request)

    assert result.body is None
    assert result.request_fingerprint is None
    assert result.payload_too_large is True


@pytest.mark.asyncio
async def test_read_invoke_body_replays_buffered_request_body_for_downstream_parsing() -> None:
    guardrails = InvokeGuardrails(
        api_rate_limit="10/minute",
        invoke_rate_limit="10/minute",
        quote_rate_limit="10/minute",
        payload_max_bytes=1024,
    )
    body = json.dumps(
        {"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": None}
    ).encode()
    request = _build_streaming_request(chunks=[body[:20], body[20:]])

    result = await guardrails._read_invoke_body(request)

    assert result.payload_too_large is False
    assert result.body == body
    assert result.request_fingerprint is not None
    assert await request.body() == body


@pytest.mark.asyncio
async def test_resolve_owner_key_uses_validated_actor_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.actor import ActorContext

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[object]:
        yield object()

    app = FastAPI()
    app.state.app_state = SimpleNamespace(db_session_factory=session_factory)
    guardrails = InvokeGuardrails(
        api_rate_limit="10/minute",
        invoke_rate_limit="10/minute",
        quote_rate_limit="10/minute",
        payload_max_bytes=1024,
    )
    request = _build_streaming_request(
        chunks=[b"{}"],
        headers=[(b"authorization", b"Bearer token")],
        app=app,
    )

    async def fake_resolve_actor(
        *, session: object, settings: object, authorization: str, touch_api_key: bool = True
    ) -> ActorContext:
        _ = session, settings, authorization, touch_api_key
        return ActorContext(account_id=42, wallet_address="0x1")

    monkeypatch.setattr(
        "app.core.guardrails.resolve_actor",
        fake_resolve_actor,
    )

    owner_key = await guardrails._resolve_owner_key(request)

    assert owner_key == "account:42"


@pytest.mark.asyncio
async def test_resolve_owner_key_falls_back_to_client_key_for_unresolved_bearer_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def session_factory() -> AsyncIterator[object]:
        yield object()

    app = FastAPI()
    app.state.app_state = SimpleNamespace(db_session_factory=session_factory)
    guardrails = InvokeGuardrails(
        api_rate_limit="10/minute",
        invoke_rate_limit="10/minute",
        quote_rate_limit="10/minute",
        payload_max_bytes=1024,
    )
    request = _build_streaming_request(
        chunks=[b"{}"],
        headers=[(b"authorization", b"Bearer stale-token")],
        app=app,
    )

    async def fake_resolve_actor(
        *, session: object, settings: object, authorization: str, touch_api_key: bool = True
    ) -> object:
        _ = session, settings, authorization, touch_api_key
        raise UnauthenticatedError("invalid access token")

    monkeypatch.setattr(
        "app.core.guardrails.resolve_actor",
        fake_resolve_actor,
    )

    owner_key = await guardrails._resolve_owner_key(request)

    assert owner_key == "client:127.0.0.1"
