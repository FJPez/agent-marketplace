from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import AppEnv, Settings
from app.core.lifespan import get_resources
from app.core.rate_limits_backend import MemoryRateLimitsBackend, RedisRateLimitsBackend
from app.main import create_app


def test_create_app_opens_resources_for_its_lifespan() -> None:
    app = create_app()

    with TestClient(app):
        assert app.title == "Agent Marketplace Backend"
        assert app.debug is False
        resources = get_resources(app)

        assert resources.settings.env is AppEnv.DEV
        assert resources.settings.title == "Agent Marketplace Backend"
        assert resources.settings.debug is False
        assert resources.db_session_factory.kw["bind"] is resources.db_engine
        assert resources.redis_client is None
        assert isinstance(resources.rate_limits_backend, MemoryRateLimitsBackend)

    assert not hasattr(app.state, "resources")


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


def test_create_app_applies_runtime_resource_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(
            jwt_secret_key="test-secret-key-with-32-bytes-123",
            db_pool_size=7,
            db_max_overflow=11,
            db_pool_timeout=25.0,
            db_pool_recycle=333,
        ),
    )
    app = create_app()

    with TestClient(app):
        pool = get_resources(app).db_engine.sync_engine.pool
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
            jwt_secret_key="test-secret-key-with-32-bytes-123",
            redis_url="redis://localhost:6379/0",
        ),
    )
    app = create_app()

    with TestClient(app):
        resources = get_resources(app)
        assert resources.redis_client is not None
        assert isinstance(resources.rate_limits_backend, RedisRateLimitsBackend)
