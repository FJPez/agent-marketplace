import pytest
from starlette.requests import Request

from app.core.rate_limits_backend import RateLimitsBackend, build_rate_limit_key


def _build_request(
    *,
    path: str = "/v1/services",
    account_id: str | None = None,
    client_host: str = "127.0.0.1",
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if account_id is not None:
        headers.append((b"x-account-id", account_id.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_build_rate_limit_key_prefers_account_id() -> None:
    request = _build_request(account_id="42", client_host="10.0.0.1")

    assert build_rate_limit_key(request) == "account:42"


@pytest.mark.asyncio
async def test_rate_limits_backend_reset_clears_recorded_hits() -> None:
    backend = RateLimitsBackend()
    request = _build_request(account_id="42")
    key = build_rate_limit_key(request)

    first_allowed = await backend.hit("1/minute", key=key, scope="global")
    second_allowed = await backend.hit("1/minute", key=key, scope="global")
    await backend.reset()
    third_allowed = await backend.hit("1/minute", key=key, scope="global")

    assert first_allowed is True
    assert second_allowed is False
    assert third_allowed is True
