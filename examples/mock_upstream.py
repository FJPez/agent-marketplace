from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Request

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.responses import Response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mock_upstream")

app = FastAPI(title="Agent Marketplace Mock Upstream")


@app.middleware("http")
async def log_request_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    logger.info(
        "received %s %s headers=%s",
        request.method,
        request.url.path,
        dict(request.headers),
    )
    return await call_next(request)


@app.post("/free-ping")
async def free_ping() -> dict[str, str]:
    return {"message": "pong", "mode": "free"}


@app.post("/paid-summary")
async def paid_summary(payload: dict[str, object]) -> dict[str, str]:
    message = payload.get("message")
    text = message if isinstance(message, str) else ""
    return {
        "summary": _build_summary(text),
        "mode": "paid",
    }


def _build_summary(message: str) -> str:
    words = message.split()
    if not words:
        return "No message provided."

    preview = " ".join(words[:8])
    if len(words) > 8:
        preview = f"{preview}..."
    return f"Summary: {preview}"


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000)
