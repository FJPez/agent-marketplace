from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Protocol

from limits import RateLimitItem, parse
from limits.aio.storage import MemoryStorage, RedisStorage
from limits.aio.strategies import FixedWindowRateLimiter

from app.core.config import Settings, get_settings
from app.core.security import hash_api_key

if TYPE_CHECKING:
    from fastapi import Request


def build_client_rate_limit_key(request: Request) -> str:
    client_host = request.client.host if request.client is not None else "unknown"
    return f"client:{client_host}"


def build_actor_rate_limit_key(request: Request) -> str:
    cached_key = getattr(request.state, "rate_limit_owner_key", None)
    if isinstance(cached_key, str):
        return cached_key

    authorization = request.headers.get("Authorization")
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme == "Bearer" and token:
            settings = get_settings()
            if token.startswith(settings.api_key_prefix):
                return f"api_key:{hash_api_key(token)}"
            return f"bearer:{hash_api_key(token)}"
    return build_client_rate_limit_key(request)


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

    async def reset(self) -> None: ...


class MemoryRateLimitsBackend:
    def __init__(self) -> None:
        self._storage = MemoryStorage()
        self._limiter = FixedWindowRateLimiter(self._storage)

    async def hit(
        self,
        limit_value: str,
        *,
        key: str,
        scope: str,
    ) -> bool:
        item = _parse_limit(limit_value)
        return await self._limiter.hit(item, scope, key)

    async def reset(self) -> None:
        await self._storage.reset()


class RedisRateLimitsBackend:
    def __init__(self, redis_url: str, *, key_prefix: str = "agent-marketplace") -> None:
        self._storage = RedisStorage(
            redis_url,
            implementation="coredis",
            key_prefix=f"{key_prefix}:rate-limits",
        )
        self._limiter = FixedWindowRateLimiter(self._storage)

    async def hit(
        self,
        limit_value: str,
        *,
        key: str,
        scope: str,
    ) -> bool:
        item = _parse_limit(limit_value)
        return await self._limiter.hit(item, scope, key)

    async def reset(self) -> None:
        await self._storage.reset()


def create_rate_limits_backend(settings: Settings) -> RateLimitsBackend:
    if settings.redis_url:
        return RedisRateLimitsBackend(
            settings.redis_url,
            key_prefix=f"agent-marketplace:{settings.env.value}",
        )
    return MemoryRateLimitsBackend()


def get_rate_limits_backend() -> RateLimitsBackend:
    return create_rate_limits_backend(get_settings())
