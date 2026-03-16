from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.types import Lifespan

from app.core.config import Settings
from app.db.session import create_engine, create_session_factory
from app.integrations.x402.facilitator_client import FacilitatorClient
from app.integrations.x402.resource_server import X402ResourceServerAdapter


@dataclass(slots=True)
class AppState:
    settings: Settings
    stack: AsyncExitStack
    db_engine: AsyncEngine | None = None
    db_session_factory: async_sessionmaker[AsyncSession] | None = None
    http_client: object | None = None
    facilitator_client: object | None = None
    x402_resource_server: object | None = None
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
    state.facilitator_client = FacilitatorClient(
        url=state.settings.x402_facilitator_url,
        http_client=state.http_client,
        cdp_api_key_id=state.settings.x402_cdp_api_key_id,
        cdp_api_key_secret=state.settings.x402_cdp_api_key_secret,
    )
    state.x402_resource_server = X402ResourceServerAdapter()


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
