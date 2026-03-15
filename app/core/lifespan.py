from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.types import Lifespan

from app.core.config import Settings
from app.db.session import create_engine, create_session_factory


@dataclass(slots=True)
class AppState:
    settings: Settings
    stack: AsyncExitStack
    db_engine: AsyncEngine | None = None
    db_session_factory: async_sessionmaker[AsyncSession] | None = None
    http_client: object | None = None
    telemetry: object | None = None


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
    state.http_client = AsyncClient()
    state.stack.push_async_callback(state.http_client.aclose)


def create_lifespan(settings: Settings) -> Lifespan[FastAPI]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with AsyncExitStack() as stack:
                state = AppState(settings=settings, stack=stack)
                app.state.app_state = state
                await _init_app_state(state)
                yield
        finally:
            if hasattr(app.state, "app_state"):
                delattr(app.state, "app_state")

    return lifespan
