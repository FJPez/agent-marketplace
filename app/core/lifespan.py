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


def create_lifespan(settings: Settings) -> Lifespan[FastAPI]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            app.state.app_state = AppState(settings=settings, stack=stack)
            # Future shared resources should be entered via this stack.
            yield
        delattr(app.state, "app_state")

    return lifespan
