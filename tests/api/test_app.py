from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient
from pydantic import SecretStr

import app.main as main_module
from app.core import lifespan as lifespan_module
from app.core.config import AppEnv, Settings
from app.core.lifespan import get_app_state
from app.integrations.payouts import BaseSepoliaUsdcPayoutExecutor
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
        assert state.http_client is not None
        assert state.facilitator_client is not None
        assert state.x402_resource_server is not None
        assert state.telemetry is None

    assert not hasattr(app.state, "app_state")


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


def test_create_app_fails_fast_for_cdp_facilitator_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_X402_CDP_API_KEY_ID", raising=False)
    monkeypatch.delenv("APP_X402_CDP_API_KEY_SECRET", raising=False)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(
            x402_facilitator_url="https://api.cdp.coinbase.com/platform/v2/x402",
        ),
    )
    app = create_app()

    with (
        pytest.raises(
            RuntimeError,
            match="APP_X402_CDP_API_KEY_ID and APP_X402_CDP_API_KEY_SECRET are required",
        ),
        TestClient(app),
    ):
        pass


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
            http_connect_timeout=2.0,
            http_read_timeout=7.0,
            http_write_timeout=9.0,
            http_pool_timeout=11.0,
            http_max_connections=50,
            http_max_keepalive_connections=15,
        ),
    )
    app = create_app()

    with TestClient(app):
        state = get_app_state(app)
        assert isinstance(state.http_client, AsyncClient)
        assert state.db_engine is not None

        pool = state.db_engine.sync_engine.pool
        pool_size = getattr(pool, "size", None)
        assert callable(pool_size)
        assert pool_size() == 7
        assert getattr(pool, "_max_overflow", None) == 11
        assert getattr(pool, "_timeout", None) == 25.0
        assert getattr(pool, "_recycle", None) == 333

        timeout = state.http_client.timeout
        assert timeout.connect == 2.0
        assert timeout.read == 7.0
        assert timeout.write == 9.0
        assert timeout.pool == 11.0
        transport = getattr(state.http_client, "_transport", None)
        http_pool = getattr(transport, "_pool", None)
        assert getattr(http_pool, "_max_connections", None) == 50
        assert getattr(http_pool, "_max_keepalive_connections", None) == 15


def test_health_ready_returns_service_unavailable_without_db_session_factory() -> None:
    app = create_app()

    with TestClient(app) as client:
        state = get_app_state(app)
        state.db_session_factory = None

        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


def test_create_app_initializes_payout_executor_for_deployed_environments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(
            env=AppEnv.STAGING,
            jwt_secret_key="test-secret-key-with-32-bytes-123",
            database_url="postgresql+asyncpg://db.internal:5432/agent_marketplace",
            siwe_domain="staging.example.com",
            payouts_enabled=True,
            payouts_rpc_url="https://rpc.example.com",
            treasury_private_key=SecretStr("0x" + "cd" * 32),
        ),
    )
    app = create_app()

    with TestClient(app):
        state = get_app_state(app)
        assert isinstance(state.payout_executor, BaseSepoliaUsdcPayoutExecutor)
