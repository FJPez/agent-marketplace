"""The API and worker started the way the Dockerfile and compose start them."""

import json
import os
import select
import signal
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from tests.fixtures.settings import MALFORMED_REDIS_URL
from tests.integration.failing_app import PAYMENT_SECRET

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUEST_ID = "entrypoint-test"


def _json_lines(output: str) -> list[dict[str, object]]:
    # uvicorn's own access and error logs are plain text; the app's are JSON.
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def _serve_one_request(app: str, path: str, *options: str) -> tuple[httpx.Response, str, str]:
    """Start uvicorn on `app`, send one GET for `path`, stop it; return the output too.

    uvicorn serves on a socket bound here, so there is no free-port race, and the
    request waits in the backlog until the app has started.
    """
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


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["sigterm", "sigint"])
def test_worker_process_stops_cleanly_on_a_signal(signum: signal.Signals) -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=PROJECT_ROOT,
        env=os.environ | {"APP_LOG_LEVEL": "INFO"},
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert process.stdout is not None
        # Signal only once the worker has installed its handlers and logged its start.
        readable, _, _ = select.select([process.stdout], [], [], 30)
        assert readable, "the worker did not log its start"
        started = json.loads(process.stdout.readline())
        process.send_signal(signum)
        output, _ = process.communicate(timeout=30)
    finally:
        process.kill()

    assert process.returncode == 0
    assert (started["logger"], started["message"]) == ("app.worker", "worker started")
    assert [entry["message"] for entry in _json_lines(output)] == [
        "worker stopping",
        "worker stopped",
    ]


def test_worker_process_logs_a_crash_as_a_json_line() -> None:
    # Logging is configured by then: opening the resources fails on the Redis URL.
    result = subprocess.run(
        [sys.executable, "-m", "app.worker"],
        cwd=PROJECT_ROOT,
        env=os.environ | {"APP_LOG_LEVEL": "INFO", "APP_REDIS_URL": MALFORMED_REDIS_URL},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    (crash,) = _json_lines(result.stdout)
    assert (crash["logger"], crash["level"], crash["message"]) == (
        "app.worker",
        "ERROR",
        "worker crashed",
    )
    assert "ValueError: Port out of range" in str(crash["exception"])
    assert result.stderr == ""


def test_worker_imports_neither_the_api_nor_fastapi() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, app.worker; print(sorted({'app.main', 'fastapi'} & sys.modules.keys()))",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "[]"
