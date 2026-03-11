from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.lifespan import create_lifespan


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.title,
        debug=settings.debug,
        lifespan=create_lifespan(settings),
    )
    app.include_router(api_router)
    return app


app = create_app()
