from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.resources import Resources, open_resources

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
    from starlette.types import Lifespan

    from app.core.config import Settings


def get_resources(app: FastAPI) -> Resources:
    resources = getattr(app.state, "resources", None)
    if not isinstance(resources, Resources):
        msg = "application resources are not open"
        raise RuntimeError(msg)
    return resources


def create_lifespan(settings: Settings) -> Lifespan[FastAPI]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_resources(settings) as resources:
            app.state.resources = resources
            try:
                yield
            finally:
                del app.state.resources

    return lifespan
