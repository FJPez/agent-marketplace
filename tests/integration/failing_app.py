"""The API plus one route that fails, for uvicorn to serve in test_entrypoints.py."""

from fastapi import FastAPI

from app.main import create_app

PAYMENT_SECRET = "sk-live-4f9a7c"


def create_failing_app() -> FastAPI:
    app = create_app()

    @app.get("/fail")
    def fail() -> None:
        msg = f"facilitator rejected X-PAYMENT: {PAYMENT_SECRET}"
        raise RuntimeError(msg)

    return app
