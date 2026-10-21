from fastapi import FastAPI

from app.api.exception_handlers import install_exception_handlers
from app.api.router import api_router
from app.core.config import get_settings
from app.core.guardrails import install_guardrails
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
    install_guardrails(app)
    # Installed last so it runs outermost and also logs and tags guardrails responses.
    install_observability(app)
    app.include_router(api_router)
    return app


app = create_app()
