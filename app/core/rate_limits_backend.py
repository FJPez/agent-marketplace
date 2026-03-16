from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from limits import RateLimitItem, parse
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import FixedWindowRateLimiter

from app.api.deps.auth import X_ACCOUNT_ID_HEADER

if TYPE_CHECKING:
    from fastapi import Request


def build_rate_limit_key(request: Request) -> str:
    account_id = request.headers.get(X_ACCOUNT_ID_HEADER)
    if account_id:
        return f"account:{account_id}"

    client_host = request.client.host if request.client is not None else "unknown"
    return f"client:{client_host}"


@lru_cache
def _parse_limit(limit_value: str) -> RateLimitItem:
    return parse(limit_value)


class RateLimitsBackend:
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


@lru_cache
def get_rate_limits_backend() -> RateLimitsBackend:
    return RateLimitsBackend()
