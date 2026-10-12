from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.exception_handlers import install_exception_handlers
from app.core.body_limit import RequestBodyLimit

MAX_BYTES = 64
TOO_LARGE_PROBLEM = {
    "type": "about:blank",
    "status": 413,
    "detail": "request body must be at most 64 bytes",
}
JSON_HEADERS = {"Content-Type": "application/json"}


def _json_body(size: int) -> bytes:
    """A JSON object of exactly `size` bytes."""
    padding = size - len(b'{"text": ""}')
    return b'{"text": "' + b"x" * padding + b'"}'


def _in_two_chunks(body: bytes) -> Iterator[bytes]:
    # A streamed body is sent chunked, so it carries no Content-Length.
    yield body[: MAX_BYTES // 2]
    yield body[MAX_BYTES // 2 :]


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    install_exception_handlers(app)

    @app.post("/parsed")
    async def read_parsed(payload: dict[str, str]) -> dict[str, int]:
        return {"length": len(payload["text"])}

    @app.post("/raw")
    async def read_raw(request: Request) -> dict[str, int]:
        return {"length": len(await request.body())}

    with TestClient(RequestBodyLimit(app, max_bytes=MAX_BYTES)) as test_client:
        yield test_client


def test_a_body_at_the_limit_is_accepted(client: TestClient) -> None:
    response = client.post("/raw", content=_json_body(MAX_BYTES), headers=JSON_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"length": MAX_BYTES}


def test_a_declared_length_over_the_limit_is_refused(client: TestClient) -> None:
    response = client.post("/raw", content=_json_body(MAX_BYTES + 1), headers=JSON_HEADERS)

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == TOO_LARGE_PROBLEM


@pytest.mark.parametrize("path", ["/parsed", "/raw"])
def test_a_body_sent_without_a_length_is_refused_once_it_passes_the_limit(
    client: TestClient,
    path: str,
) -> None:
    body = _in_two_chunks(_json_body(MAX_BYTES + 1))

    response = client.post(path, content=body, headers=JSON_HEADERS)

    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == TOO_LARGE_PROBLEM
