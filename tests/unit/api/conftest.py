from collections.abc import Callable

import pytest
from fastapi import FastAPI

from app.api.exception_handlers import install_exception_handlers

AppFactory = Callable[[Exception], FastAPI]


@pytest.fixture
def handler_app_factory() -> AppFactory:
    """Build an app with the global exception handlers and one route that raises."""

    def make_app(exc: Exception) -> FastAPI:
        app = FastAPI()
        install_exception_handlers(app)

        @app.get("/boom")
        async def boom() -> None:
            raise exc

        return app

    return make_app
