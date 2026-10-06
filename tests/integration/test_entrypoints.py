"""The API started the way the Dockerfile and compose start it."""

import json
import os
import socket
import subprocess
import sys

import httpx
from tests.fixtures.settings import PROJECT_ROOT
from tests.integration.failing_app import PAYMENT_SECRET

REQUEST_ID = "entrypoint-test"


def _json_lines(output: str) -> list[dict[str, object]]:
    # uvicorn's own access and error logs are plain text; the app's are JSON.
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def _serve_one_request(app: str, path: str, *options: str) -> tuple[httpx.Response, str, str]:
    """Start uvicorn on `app`, send one GET for `path`, stop it; return the output too."""
    # uvicorn serves on a socket bound here, so the request waits in its backlog.
    with socket.create_server(("127.0.0.1", 0)) as server:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", app, *options, "--fd", str(server.fileno())],
            cwd=PROJECT_ROOT,
            env=os.environ | {"APP_LOG_LEVEL": "INFO"},
            pass_fds=[server.fileno()],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            response = httpx.get(
                f"http://127.0.0.1:{server.getsockname()[1]}{path}",
                headers={"X-Request-ID": REQUEST_ID},
                timeout=30,
            )
            process.terminate()
            stdout, stderr = process.communicate(timeout=30)
        finally:
            process.kill()
    return response, stdout, stderr


def test_api_process_writes_request_logs_as_json_lines() -> None:
    response, stdout, _ = _serve_one_request("app.main:app", "/health")

    assert response.status_code == 200
    request_logs = [
        entry for entry in _json_lines(stdout) if entry["logger"] == "app.core.observability"
    ]
    assert len(request_logs) == 1
    assert {
        "level": "INFO",
        "message": "request completed",
        "request_id": REQUEST_ID,
        "method": "GET",
        "path": "/health",
        "status_code": 200,
    }.items() <= request_logs[0].items()


def test_api_process_logs_an_unhandled_error_once_and_redacted() -> None:
    response, stdout, stderr = _serve_one_request(
        "tests.integration.failing_app:create_failing_app", "/fail", "--factory"
    )

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "/problems/internal_error"
    assert PAYMENT_SECRET not in stdout
    assert PAYMENT_SECRET not in stderr
    # The app handled the error, so uvicorn has no traceback of its own to print.
    assert "Exception in ASGI application" not in stderr
    (error_log,) = [entry for entry in _json_lines(stdout) if entry["level"] == "ERROR"]
    assert {
        "logger": "app.core.observability",
        "message": "request failed",
        "request_id": REQUEST_ID,
        "path": "/fail",
    }.items() <= error_log.items()
    assert "RuntimeError: facilitator rejected X-PAYMENT: [REDACTED]" in str(error_log["exception"])
