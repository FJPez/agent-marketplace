from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from starlette.types import Lifespan

from app.core.config import Settings


@dataclass(slots=True)
class AppState:
    settings: Settings
    stack: AsyncExitStack
    db_engine: object | None = None
    http_client: object | None = None
    telemetry: object | None = None


def get_app_state(app: FastAPI) -> AppState:
    state = getattr(app.state, "app_state", None)
    if not isinstance(state, AppState):
        msg = "app state is not initialized"
        raise RuntimeError(msg)
    return state


async def _init_app_state(state: AppState) -> None:
    # Future shared resources should be entered via this stack.
    _ = state


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
