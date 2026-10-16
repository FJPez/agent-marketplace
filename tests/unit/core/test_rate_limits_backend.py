import pytest

from app.core.config import Settings
from app.core.rate_limits_backend import (
    MemoryRateLimitsBackend,
    RedisRateLimitsBackend,
    create_rate_limits_backend,
)


@pytest.mark.asyncio
async def test_rate_limits_backend_reset_clears_recorded_hits() -> None:
    backend = MemoryRateLimitsBackend()
    key = "client:127.0.0.1"

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
    backend = create_rate_limits_backend(
        Settings(
            jwt_secret_key="test-secret-key-with-32-bytes-123",
            redis_url=None,
        )
    )

    assert isinstance(backend, MemoryRateLimitsBackend)


def test_create_rate_limits_backend_supports_redis_backends() -> None:
    backend = create_rate_limits_backend(
        Settings(
            jwt_secret_key="test-secret-key-with-32-bytes-123",
            redis_url="redis://localhost:6379/0",
        )
    )

    assert isinstance(backend, RedisRateLimitsBackend)
