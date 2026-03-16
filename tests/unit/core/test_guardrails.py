from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.requests import Request

from app.core.guardrails import InvokeGuardrails


def _build_streaming_request(
    *,
    chunks: list[bytes],
    headers: list[tuple[bytes, bytes]] | None = None,
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
