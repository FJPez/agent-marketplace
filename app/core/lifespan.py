from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import coredis

from app.core.rate_limits_backend import RateLimitsBackend, create_rate_limits_backend
from app.db.session import create_engine, create_session_factory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from coredis.client.basic import Redis
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
    from starlette.types import Lifespan

    from app.core.config import Settings


@dataclass(slots=True)
class AppState:
    settings: Settings
    stack: AsyncExitStack
    db_engine: AsyncEngine | None = None
    db_session_factory: async_sessionmaker[AsyncSession] | None = None
    redis_client: Redis[str] | None = None
    rate_limits_backend: RateLimitsBackend | None = None


def get_app_state(app: FastAPI) -> AppState:
    state = getattr(app.state, "app_state", None)
    if not isinstance(state, AppState):
        msg = "app state is not initialized"
        raise RuntimeError(msg)
    return state


async def _init_app_state(state: AppState) -> None:
    state.db_engine = create_engine(state.settings)
    state.db_session_factory = create_session_factory(state.db_engine)
    state.stack.push_async_callback(state.db_engine.dispose)
    if state.redis_client is not None:
        state.stack.callback(state.redis_client.connection_pool.disconnect)


def create_redis_client(settings: Settings) -> Redis[str] | None:
    if not settings.redis_url:
        return None
    return coredis.Redis.from_url(settings.redis_url, decode_responses=True)


def create_lifespan(
    settings: Settings,
    *,
    redis_client: Redis[str] | None = None,
    rate_limits_backend: RateLimitsBackend | None = None,
) -> Lifespan[FastAPI]:
    if redis_client is None:
        redis_client = create_redis_client(settings)
    if rate_limits_backend is None:
        rate_limits_backend = create_rate_limits_backend(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with AsyncExitStack() as stack:
                state = AppState(
                    settings=settings,
                    stack=stack,
                    redis_client=redis_client,
                    rate_limits_backend=rate_limits_backend,
                )
                app.state.app_state = state
                await _init_app_state(state)
                yield
        finally:
            if hasattr(app.state, "app_state"):
                delattr(app.state, "app_state")

    return lifespan
