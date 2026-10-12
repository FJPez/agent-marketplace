from fastapi import FastAPI

from app.api.exception_handlers import install_exception_handlers
from app.api.router import api_router
from app.core.body_limit import RequestBodyLimit
from app.core.config import get_settings
from app.core.lifespan import create_lifespan
from app.core.logging import configure_logging
from app.core.observability import install_observability


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title=settings.title,
        debug=settings.debug,
        lifespan=create_lifespan(settings),
    )
    install_exception_handlers(app)
    # ty 0.0.21 matches no class, Starlette's own included, to the middleware factory protocol.
    app.add_middleware(
        RequestBodyLimit,  # ty: ignore[invalid-argument-type]
        max_bytes=settings.max_request_body_bytes,
    )
    # Installed last so it runs outermost and also logs and tags a body limit refusal.
    install_observability(app)
    app.include_router(api_router)
    return app


app = create_app()
