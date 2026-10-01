from __future__ import annotations

import math
import time
from functools import lru_cache
from typing import Protocol

from fastapi import Request
from limits import RateLimitItem, parse
from limits.aio.storage import MemoryStorage, RedisStorage, Storage
from limits.aio.strategies import FixedWindowRateLimiter

from app.core.config import Settings, get_settings


def build_client_rate_limit_key(request: Request) -> str:
    client_host = request.client.host if request.client is not None else "unknown"
    return f"client:{client_host}"


@lru_cache
def _parse_limit(limit_value: str) -> RateLimitItem:
    return parse(limit_value)


class RateLimitsBackend(Protocol):
    async def hit(
        self,
        limit_value: str,
        *,
        key: str,
        scope: str,
    ) -> bool: ...

    async def seconds_until_reset(
        self,
        limit_value: str,
        *,
        key: str,
        scope: str,
    ) -> int:
        """Whole seconds, at least 1, until the window for `key` resets (`Retry-After`)."""

    async def reset(self) -> None: ...


class FixedWindowRateLimitsBackend:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._limiter = FixedWindowRateLimiter(storage)

    async def hit(
        self,
        limit_value: str,
        *,
        key: str,
        scope: str,
    ) -> bool:
        return await self._limiter.hit(_parse_limit(limit_value), scope, key)

    async def seconds_until_reset(
        self,
        limit_value: str,
        *,
        key: str,
        scope: str,
    ) -> int:
        window = await self._limiter.get_window_stats(_parse_limit(limit_value), scope, key)
        # Never below 1, so a window that expires during this lookup still asks for a wait.
        return max(1, math.ceil(window.reset_time - time.time()))

    async def reset(self) -> None:
        await self._storage.reset()


class MemoryRateLimitsBackend(FixedWindowRateLimitsBackend):
    def __init__(self) -> None:
        super().__init__(MemoryStorage())


class RedisRateLimitsBackend(FixedWindowRateLimitsBackend):
    def __init__(self, redis_url: str, *, key_prefix: str = "agent-marketplace") -> None:
        super().__init__(
            RedisStorage(
                redis_url,
                implementation="coredis",
                key_prefix=f"{key_prefix}:rate-limits",
            )
        )


def create_rate_limits_backend(settings: Settings) -> RateLimitsBackend:
    if settings.redis_url:
        return RedisRateLimitsBackend(
            settings.redis_url,
            key_prefix=f"agent-marketplace:{settings.env.value}",
        )
    return MemoryRateLimitsBackend()


def get_rate_limits_backend() -> RateLimitsBackend:
    return create_rate_limits_backend(get_settings())
