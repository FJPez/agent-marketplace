from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.guardrails import InvokeGuardrails, install_guardrails
from app.core.lifespan import create_lifespan
from app.core.observability import install_observability


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.title,
        debug=settings.debug,
        lifespan=create_lifespan(settings),
    )
    install_observability(app)
    install_guardrails(
        app,
        guardrails=InvokeGuardrails(
            api_rate_limit=settings.api_rate_limit,
            invoke_rate_limit=settings.invoke_rate_limit,
            quote_rate_limit=settings.quote_rate_limit,
            payload_max_bytes=settings.invoke_payload_max_bytes,
        ),
    )
    app.include_router(api_router)
    return app


app = create_app()
