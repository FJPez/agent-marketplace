from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from limits import RateLimitItem, parse
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import FixedWindowRateLimiter

from app.core.config import get_settings
from app.core.security import AuthTokenType, decode_jwt, hash_api_key

if TYPE_CHECKING:
    from fastapi import Request


def build_client_rate_limit_key(request: Request) -> str:
    client_host = request.client.host if request.client is not None else "unknown"
    return f"client:{client_host}"


def build_actor_rate_limit_key(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme == "Bearer" and token:
            settings = get_settings()
            if token.startswith(settings.api_key_prefix):
                return f"api_key:{hash_api_key(token)}"
            try:
                claims = decode_jwt(
                    token,
                    secret_key=settings.jwt_secret_key,
                    expected_token_type=AuthTokenType.ACCESS,
                )
            except Exception:
                pass
            else:
                return f"account:{claims.account_id}"
    return build_client_rate_limit_key(request)


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
