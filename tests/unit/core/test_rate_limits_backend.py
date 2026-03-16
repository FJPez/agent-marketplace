import pytest
from starlette.requests import Request

from app.core.rate_limits_backend import (
    RateLimitsBackend,
    build_actor_rate_limit_key,
    build_client_rate_limit_key,
)
from app.core.security import AuthTokenType, create_jwt


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


def test_build_actor_rate_limit_key_prefers_verified_jwt_subject() -> None:
    token = create_jwt(
        secret_key="dev-jwt-secret-key-with-32-bytes-min",
        account_id=42,
        wallet_address="0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6",
        token_version=1,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=900,
    )
    request = _build_request(authorization=f"Bearer {token}")

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
