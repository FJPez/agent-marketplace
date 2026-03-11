from fastapi.testclient import TestClient

from app.core.config import AppEnv
from app.core.lifespan import get_app_state
from app.main import create_app


def test_create_app_starts_with_lifespan_state() -> None:
    app = create_app()

    with TestClient(app) as client:
        assert client.app.title == "Agent Marketplace Backend"
        assert client.app.debug is False
        state = get_app_state(client.app)

        assert state.settings.env is AppEnv.DEV
        assert state.settings.title == "Agent Marketplace Backend"
        assert state.settings.debug is False
        assert state.db_engine is None
        assert state.http_client is None
        assert state.telemetry is None

    assert not hasattr(app.state, "app_state")
