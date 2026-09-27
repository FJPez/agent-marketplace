"""The API and worker started the way the Dockerfile and compose start them."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx

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
