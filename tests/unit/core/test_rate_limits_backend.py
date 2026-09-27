import importlib
import importlib.util

import pytest
from starlette.requests import Request

from app.core.config import Settings
from app.core.rate_limits_backend import MemoryRateLimitsBackend, build_client_rate_limit_key


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


@pytest.mark.asyncio
async def test_rate_limits_backend_reset_clears_recorded_hits() -> None:
    module = importlib.import_module("app.core.rate_limits_backend")
    backend_type = getattr(module, "MemoryRateLimitsBackend", None)

    assert backend_type is not None

    backend = backend_type()
    request = _build_request()
    key = build_client_rate_limit_key(request)

    first_allowed = await backend.hit("1/minute", key=key, scope="global")
    second_allowed = await backend.hit("1/minute", key=key, scope="global")
    await backend.reset()
    third_allowed = await backend.hit("1/minute", key=key, scope="global")

    assert first_allowed is True
    assert second_allowed is False
    assert third_allowed is True


async def test_memory_backend_reports_seconds_until_the_window_resets() -> None:
    backend = MemoryRateLimitsBackend()

    await backend.hit("1/minute", key="client:10.0.0.1", scope="global")
    await backend.hit("1/minute", key="client:10.0.0.1", scope="global")
    retry_after = await backend.seconds_until_reset(
        "1/minute",
        key="client:10.0.0.1",
        scope="global",
    )

    assert 55 <= retry_after <= 60


async def test_memory_backend_never_reports_less_than_one_second() -> None:
    backend = MemoryRateLimitsBackend()

    retry_after = await backend.seconds_until_reset(
        "1/minute",
        key="client:never-hit",
        scope="global",
    )

    assert retry_after == 1


def test_create_rate_limits_backend_defaults_to_memory_without_redis_url() -> None:
    module = importlib.import_module("app.core.rate_limits_backend")
    backend_factory = getattr(module, "create_rate_limits_backend", None)
    memory_backend_type = getattr(module, "MemoryRateLimitsBackend", None)

    assert callable(backend_factory)
    assert memory_backend_type is not None

    backend = backend_factory(
        Settings(
            jwt_secret_key="test-secret-key-with-32-bytes-123",
            redis_url=None,
        )
    )

    assert isinstance(backend, memory_backend_type)


def test_create_rate_limits_backend_supports_redis_backends() -> None:
    module = importlib.import_module("app.core.rate_limits_backend")
    backend_factory = getattr(module, "create_rate_limits_backend", None)
    redis_backend_type = getattr(module, "RedisRateLimitsBackend", None)

    assert callable(backend_factory)
    assert redis_backend_type is not None

    backend = backend_factory(
        Settings(
            jwt_secret_key="test-secret-key-with-32-bytes-123",
            redis_url="redis://localhost:6379/0",
        )
    )

    assert isinstance(backend, redis_backend_type)


def test_rate_limits_backend_module_exposes_backend_abstractions() -> None:
    spec = importlib.util.find_spec("app.core.rate_limits_backend")

    assert spec is not None

    module = importlib.import_module("app.core.rate_limits_backend")

    assert getattr(module, "MemoryRateLimitsBackend", None) is not None
    assert getattr(module, "RedisRateLimitsBackend", None) is not None
    assert callable(getattr(module, "create_rate_limits_backend", None))
