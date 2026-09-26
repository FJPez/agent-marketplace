from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main_module
from app.core import lifespan as lifespan_module
from app.core.config import AppEnv, Settings
from app.core.lifespan import get_app_state
from app.main import create_app


def test_create_app_starts_with_lifespan_state() -> None:
    app = create_app()

    with TestClient(app):
        assert app.title == "Agent Marketplace Backend"
        assert app.debug is False
        state = get_app_state(app)

        assert state.settings.env is AppEnv.DEV
        assert state.settings.title == "Agent Marketplace Backend"
        assert state.settings.debug is False
        assert state.db_engine is not None
        assert state.db_session_factory is not None
        assert state.redis_client is None
        assert state.rate_limits_backend is not None

    assert not hasattr(app.state, "app_state")


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


def test_create_lifespan_cleans_up_state_on_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_init(state: lifespan_module.AppState) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(lifespan_module, "_init_app_state", fail_init, raising=False)
    app = FastAPI(lifespan=lifespan_module.create_lifespan(Settings()))

    with pytest.raises(RuntimeError, match="boom"), TestClient(app):
        pass

    assert not hasattr(app.state, "app_state")


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
        state = get_app_state(app)
        assert state.db_engine is not None

        pool = state.db_engine.sync_engine.pool
        pool_size = getattr(pool, "size", None)
        assert callable(pool_size)
        assert pool_size() == 7
        assert getattr(pool, "_max_overflow", None) == 11
        assert getattr(pool, "_timeout", None) == 25.0
        assert getattr(pool, "_recycle", None) == 333


def test_create_app_initializes_redis_client_and_rate_limit_backend_when_configured(
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
        state = get_app_state(app)
        assert state.redis_client is not None
        assert state.rate_limits_backend is not None


def test_health_ready_returns_service_unavailable_without_db_session_factory() -> None:
    app = create_app()

    with TestClient(app) as client:
        state = get_app_state(app)
        state.db_session_factory = None

        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
