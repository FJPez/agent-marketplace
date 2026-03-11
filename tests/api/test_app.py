from fastapi.testclient import TestClient

from app.main import create_app


def test_create_app_starts_with_default_settings() -> None:
    with TestClient(create_app()) as client:
        assert client.app.title == "Agent Marketplace Backend"
        assert client.app.debug is False
