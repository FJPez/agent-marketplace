"""Refuse a request body over the size limit before it is held in memory."""

from fastapi import HTTPException, status
from fastapi.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.problems import ProblemResponse


class RequestBodyLimit:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.detail = f"request body must be at most {max_bytes} bytes"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdecimal() and int(declared) > self.max_bytes:
            response = ProblemResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=self.detail,
            )
            await response(scope, receive, send)
            return

        received = 0

        # A body can arrive with no Content-Length, so the bytes are counted as well.
        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # An HTTPException is the one error FastAPI passes through from a body read.
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=self.detail,
                    )
            return message

        await self.app(scope, limited_receive, send)
