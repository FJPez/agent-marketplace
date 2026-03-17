import httpx
import pytest
from scripts.deployment_smoke import (
    SmokeCheckError,
    parse_args,
    run_smoke_checks,
)


def test_run_smoke_checks_accepts_expected_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/auth/nonce":
            assert request.url.params["address"] == "0x0000000000000000000000000000000000000001"
            return httpx.Response(200, json={"nonce": "nonce-123"})
        if request.url.path == "/v1/services":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "not found"})

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://example.com",
    ) as client:
        results = run_smoke_checks(client)

    assert results == ["live ok", "ready ok", "auth nonce ok", "/v1/services ok"]


def test_run_smoke_checks_rejects_invalid_public_db_route_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/health/ready":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/auth/nonce":
            return httpx.Response(200, json={"nonce": "nonce-123"})
        if request.url.path == "/v1/services":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404, json={"detail": "not found"})

    with (
        httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://example.com",
        ) as client,
        pytest.raises(SmokeCheckError, match="expected a JSON array"),
    ):
        run_smoke_checks(client)


def test_parse_args_reads_base_url_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMOKE_BASE_URL", "https://deploy.example.com/")

    args = parse_args([])

    assert args.base_url == "https://deploy.example.com"
