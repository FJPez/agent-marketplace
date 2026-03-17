from __future__ import annotations

import asyncio
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from coredis.tokens import PureToken

if TYPE_CHECKING:
    import coredis

    from app.core.config import Settings

_COMPARE_AND_DELETE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
_DEFAULT_INVOKE_SUBMISSION_TTL_SECONDS = 3660


class SubmissionAcquireResult(Enum):
    ACQUIRED = "acquired"
    REQUEST_IN_PROGRESS = "request_in_progress"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"


class InvokeSubmissionBackend(Protocol):
    async def acquire(
        self,
        submission_key: str,
        request_fingerprint: str,
    ) -> SubmissionAcquireResult: ...

    async def release(self, submission_key: str, request_fingerprint: str) -> None: ...

    async def reset(self) -> None: ...


class MemoryInvokeSubmissionBackend:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._in_flight_requests: dict[str, str] = {}

    async def acquire(
        self,
        submission_key: str,
        request_fingerprint: str,
    ) -> SubmissionAcquireResult:
        async with self._lock:
            existing_fingerprint = self._in_flight_requests.get(submission_key)
            if existing_fingerprint is None:
                self._in_flight_requests[submission_key] = request_fingerprint
                return SubmissionAcquireResult.ACQUIRED
            if existing_fingerprint == request_fingerprint:
                return SubmissionAcquireResult.REQUEST_IN_PROGRESS
            return SubmissionAcquireResult.IDEMPOTENCY_KEY_REUSED

    async def release(self, submission_key: str, request_fingerprint: str) -> None:
        async with self._lock:
            if self._in_flight_requests.get(submission_key) == request_fingerprint:
                self._in_flight_requests.pop(submission_key, None)

    async def reset(self) -> None:
        async with self._lock:
            self._in_flight_requests.clear()


class RedisInvokeSubmissionBackend:
    def __init__(
        self,
        redis_client: coredis.Redis[str],
        *,
        ttl_seconds: int,
        key_prefix: str = "agent-marketplace:invoke-submissions",
    ) -> None:
        self._redis_client = redis_client
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    def _build_key(self, submission_key: str) -> str:
        return f"{self._key_prefix}:{submission_key}"

    async def acquire(
        self,
        submission_key: str,
        request_fingerprint: str,
    ) -> SubmissionAcquireResult:
        redis_key = self._build_key(submission_key)
        was_set = await self._redis_client.set(
            redis_key,
            request_fingerprint,
            condition=PureToken.NX,
            ex=self._ttl_seconds,
        )
        if was_set:
            return SubmissionAcquireResult.ACQUIRED

        existing_fingerprint = await self._redis_client.get(redis_key)
        if existing_fingerprint == request_fingerprint:
            return SubmissionAcquireResult.REQUEST_IN_PROGRESS
        return SubmissionAcquireResult.IDEMPOTENCY_KEY_REUSED

    async def release(self, submission_key: str, request_fingerprint: str) -> None:
        await self._redis_client.eval(
            _COMPARE_AND_DELETE_SCRIPT,
            keys=(self._build_key(submission_key),),
            args=(request_fingerprint,),
        )

    async def reset(self) -> None:
        keys = [key async for key in self._redis_client.scan_iter(match=f"{self._key_prefix}:*")]
        if keys:
            await self._redis_client.delete(tuple(keys))


def create_invoke_submission_backend(
    settings: Settings,
    *,
    redis_client: coredis.Redis[str] | None,
) -> InvokeSubmissionBackend:
    if settings.redis_url and redis_client is not None:
        return RedisInvokeSubmissionBackend(
            redis_client,
            ttl_seconds=_DEFAULT_INVOKE_SUBMISSION_TTL_SECONDS,
            key_prefix=f"agent-marketplace:{settings.env.value}:invoke-submissions",
        )
    return MemoryInvokeSubmissionBackend()
