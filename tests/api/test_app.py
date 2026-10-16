from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request
from tests.fixtures.settings import SettingsEnvFactory

import app.main as main_module
from app.api.deps.dns import get_dns_resolver
from app.core.config import AppEnv, Settings
from app.core.rate_limits_backend import MemoryRateLimitsBackend, RedisRateLimitsBackend
from app.core.request_id import REQUEST_ID_HEADER
from app.core.resources import Resources
from app.main import create_app


def _resources(client: TestClient) -> Resources:
    # The lifespan state the client copies into each request it sends.
    resources = client.app_state["resources"]
    assert isinstance(resources, Resources)
    return resources


def test_create_app_opens_resources_for_its_lifespan() -> None:
    app = create_app()

    with TestClient(app) as client:
        assert app.title == "Agent Marketplace Backend"
        assert app.debug is False
        resources = _resources(client)

        assert resources.settings.env is AppEnv.DEV
        assert resources.settings.title == "Agent Marketplace Backend"
        assert resources.settings.debug is False
        assert resources.db_session_factory().bind is resources.db_engine
        assert resources.redis_client is None
        assert isinstance(resources.rate_limits_backend, MemoryRateLimitsBackend)


def test_the_dns_resolver_dependency_serves_the_resolver_the_app_opened() -> None:
    with TestClient(create_app()) as client:
        request = Request({"type": "http", "state": client.app_state})

        assert get_dns_resolver(request) is _resources(client).dns_resolver


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/invoke/some-service"),
        ("GET", "/v1/invocations"),
        ("GET", "/v1/invocations/1"),
        ("POST", "/v1/services/some-service/quote"),
        ("GET", "/v1/provider/earnings"),
        ("GET", "/v1/provider/ledger"),
        ("GET", "/v1/provider/payouts"),
        ("POST", "/v1/provider/payouts"),
    ],
)
def test_retired_execution_routes_are_not_served(method: str, path: str) -> None:
    with TestClient(create_app()) as client:
        response = client.request(method, path, json={})

    assert response.status_code == 404


def test_an_oversized_request_body_is_refused_and_keeps_its_request_id(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(env={"APP_MAX_REQUEST_BODY_BYTES": "64"})

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/auth/verify",
            content=b"x" * 65,
            headers={REQUEST_ID_HEADER: "request-413"},
        )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers[REQUEST_ID_HEADER] == "request-413"


def test_create_app_applies_runtime_resource_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(
            jwt_secret_key=SecretStr("test-secret-key-with-32-bytes-123"),
            db_pool_size=7,
            db_max_overflow=11,
            db_pool_timeout=25.0,
            db_pool_recycle=333,
        ),
    )
    app = create_app()

    with TestClient(app) as client:
        pool = _resources(client).db_engine.sync_engine.pool
        pool_size = getattr(pool, "size", None)
        assert callable(pool_size)
        assert pool_size() == 7
        assert getattr(pool, "_max_overflow", None) == 11
        assert getattr(pool, "_timeout", None) == 25.0
        assert getattr(pool, "_recycle", None) == 333


def test_create_app_opens_redis_client_and_rate_limit_backend_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(
            jwt_secret_key=SecretStr("test-secret-key-with-32-bytes-123"),
            redis_url="redis://localhost:6379/0",
        ),
    )
    app = create_app()

    with TestClient(app) as client:
        resources = _resources(client)
        assert resources.redis_client is not None
        assert isinstance(resources.rate_limits_backend, RedisRateLimitsBackend)
