from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.types import Lifespan

from app.core.config import Settings
from app.core.resources import Resources, open_resources


def get_resources(request: Request) -> Resources:
    resources = getattr(request.state, "resources", None)
    if not isinstance(resources, Resources):
        msg = "application resources are not open"
        raise RuntimeError(msg)
    return resources


def create_lifespan(settings: Settings) -> Lifespan[FastAPI]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, Resources]]:
        # Lifespan state: Starlette copies it into every request's `request.state`.
        async with open_resources(settings) as resources:
            yield {"resources": resources}

    return lifespan
