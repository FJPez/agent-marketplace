"""The process-wide resources the API opens for its lifetime."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import coredis
from coredis.client.basic import Redis
from dns.asyncresolver import Resolver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.rate_limits_backend import RateLimitsBackend, create_rate_limits_backend
from app.core.request_validation import ProcessRequestValidationPool
from app.db.session import create_engine, create_session_factory
from app.integrations.providers.dns import DnsPythonResolver, DnsResolver


@dataclass(frozen=True, slots=True)
class Resources:
    settings: Settings
    db_engine: AsyncEngine
    db_session_factory: async_sessionmaker[AsyncSession]
    redis_client: Redis[str] | None
    rate_limits_backend: RateLimitsBackend
    dns_resolver: DnsResolver
    request_validation_pool: ProcessRequestValidationPool


@asynccontextmanager
async def open_resources(settings: Settings) -> AsyncIterator[Resources]:
    """Build the resources and close them on exit; nothing connects until first use."""
    async with AsyncExitStack() as stack:
        db_engine = create_engine(settings)
        stack.push_async_callback(db_engine.dispose)

        redis_client = None
        if settings.redis_url:
            redis_client = coredis.Redis.from_url(settings.redis_url, decode_responses=True)
            stack.callback(redis_client.connection_pool.disconnect)

        # Reads the system's nameservers; each query gives up after 5 s, retries included.
        system_resolver = Resolver()
        system_resolver.lifetime = 5.0

        # Its worker processes start on first use, so a process that validates no request
        # body never starts them.
        request_validation_pool = ProcessRequestValidationPool(
            workers=settings.request_validation_workers,
            timeout_seconds=settings.request_validation_timeout_ms / 1000,
            compile_timeout_seconds=settings.request_schema_compile_timeout_ms / 1000,
        )
        stack.push_async_callback(request_validation_pool.close)

        yield Resources(
            settings=settings,
            db_engine=db_engine,
            db_session_factory=create_session_factory(db_engine),
            redis_client=redis_client,
            rate_limits_backend=create_rate_limits_backend(settings),
            dns_resolver=DnsPythonResolver(system_resolver),
            request_validation_pool=request_validation_pool,
        )
