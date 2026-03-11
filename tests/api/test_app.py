import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
        assert state.http_client is None
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
