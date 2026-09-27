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

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _json_lines(output: str) -> list[dict[str, object]]:
    # uvicorn's own access and error logs are plain text; the app's are JSON.
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def test_api_process_writes_request_logs_as_json_lines() -> None:
    # uvicorn serves on a socket bound here, so there is no free-port race, and the
    # request below waits in the backlog until the app has started.
    with socket.create_server(("127.0.0.1", 0)) as server:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--fd", str(server.fileno())],
            cwd=PROJECT_ROOT,
            env=os.environ | {"APP_LOG_LEVEL": "INFO"},
            pass_fds=[server.fileno()],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            port = server.getsockname()[1]
            response = httpx.get(
                f"http://127.0.0.1:{port}/health",
                headers={"X-Request-ID": "entrypoint-test"},
                timeout=30,
            )
        finally:
            process.terminate()
            output, _ = process.communicate(timeout=30)

    assert response.status_code == 200
    request_logs = [
        entry for entry in _json_lines(output) if entry["logger"] == "app.core.observability"
    ]
    assert len(request_logs) == 1
    assert {
        "level": "INFO",
        "message": "request completed",
        "request_id": "entrypoint-test",
        "method": "GET",
        "path": "/health",
        "status_code": 200,
    }.items() <= request_logs[0].items()


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
