from typing import Any

from fastapi import FastAPI

from app.api.exception_handlers import install_exception_handlers
from app.api.router import api_router
from app.core.config import get_settings
from app.core.guardrails import ApiGuardrails, install_guardrails
from app.core.lifespan import create_lifespan, create_redis_client
from app.core.observability import install_observability
from app.core.problems import document_problem_responses
from app.core.rate_limits_backend import create_rate_limits_backend


class MarketplaceApi(FastAPI):
    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is None:
            document_problem_responses(super().openapi())
        return super().openapi()


def create_app() -> FastAPI:
    settings = get_settings()
    redis_client = create_redis_client(settings)
    rate_limits_backend = create_rate_limits_backend(settings)
    app = MarketplaceApi(
        title=settings.title,
        debug=settings.debug,
        lifespan=create_lifespan(
            settings,
            redis_client=redis_client,
            rate_limits_backend=rate_limits_backend,
        ),
    )
    install_exception_handlers(app)
    install_guardrails(
        app,
        guardrails=ApiGuardrails(
            api_rate_limit=settings.api_rate_limit,
            rate_limits_backend=rate_limits_backend,
        ),
    )
    # Installed last so it runs outermost and also logs and tags guardrails responses.
    install_observability(app)
    app.include_router(api_router)
    return app


app = create_app()
