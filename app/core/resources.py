"""The process-wide resources shared by the API and the worker.

Framework-free on purpose: the worker (`python -m app.worker`) opens the same
resources as the API lifespan without importing FastAPI or `app.main`.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import coredis
import dns.asyncresolver

from app.core.rate_limits_backend import RateLimitsBackend, create_rate_limits_backend
from app.core.request_body_validation import RequestValidationPool
from app.db.session import create_engine, create_session_factory
from app.integrations.providers.dns import DnsPythonResolver, DnsResolver

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from coredis.client.basic import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class Resources:
    settings: Settings
    db_engine: AsyncEngine
    db_session_factory: async_sessionmaker[AsyncSession]
    redis_client: Redis[str] | None
    rate_limits_backend: RateLimitsBackend
    dns_resolver: DnsResolver
    request_validation_pool: RequestValidationPool


@asynccontextmanager
async def open_resources(settings: Settings) -> AsyncIterator[Resources]:
    """Build the resources and close them on exit.

    Nothing connects here: the engine and the Redis clients connect on first use, so
    opening succeeds even while a dependency is down (readiness reports it). Each
    resource's cleanup is registered immediately after it is created, so a failure
    partway through startup still releases whatever was already opened.
    """
    async with AsyncExitStack() as stack:
        db_engine = create_engine(settings)
        stack.push_async_callback(db_engine.dispose)

        redis_client = None
        if settings.redis_url:
            redis_client = coredis.Redis.from_url(settings.redis_url, decode_responses=True)
            stack.callback(redis_client.connection_pool.disconnect)

        # Reads the system's nameservers; each query gives up after 5 s, retries included.
        system_resolver = dns.asyncresolver.Resolver()
        system_resolver.lifetime = 5.0

        # Its worker processes start on first use, so a process that validates no request
        # body never starts them.
        request_validation_pool = RequestValidationPool(
            workers=settings.request_validation_workers,
            timeout_seconds=settings.request_validation_timeout_ms / 1000,
        )
        stack.callback(request_validation_pool.close)

        yield Resources(
            settings=settings,
            db_engine=db_engine,
            db_session_factory=create_session_factory(db_engine),
            redis_client=redis_client,
            rate_limits_backend=create_rate_limits_backend(settings),
            dns_resolver=DnsPythonResolver(system_resolver),
            request_validation_pool=request_validation_pool,
        )
