import pytest
from starlette.requests import Request

from app.core.rate_limits_backend import (
    RateLimitsBackend,
    build_actor_rate_limit_key,
    build_client_rate_limit_key,
)


def _build_request(
    *,
    path: str = "/v1/services",
    authorization: str | None = None,
    client_host: str = "127.0.0.1",
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_build_client_rate_limit_key_uses_client_host() -> None:
    request = _build_request(client_host="10.0.0.1")

    assert build_client_rate_limit_key(request) == "client:10.0.0.1"


def test_build_actor_rate_limit_key_prefers_cached_owner_key() -> None:
    request = _build_request()
    request.state.rate_limit_owner_key = "account:42"

    assert build_actor_rate_limit_key(request) == "account:42"


def test_build_actor_rate_limit_key_hashes_api_key_tokens() -> None:
    request = _build_request(authorization="Bearer amp_example")

    assert build_actor_rate_limit_key(request).startswith("api_key:")


@pytest.mark.asyncio
async def test_rate_limits_backend_reset_clears_recorded_hits() -> None:
    backend = RateLimitsBackend()
    request = _build_request()
    key = build_client_rate_limit_key(request)

    first_allowed = await backend.hit("1/minute", key=key, scope="global")
    second_allowed = await backend.hit("1/minute", key=key, scope="global")
    await backend.reset()
    third_allowed = await backend.hit("1/minute", key=key, scope="global")

    assert first_allowed is True
    assert second_allowed is False
    assert third_allowed is True
