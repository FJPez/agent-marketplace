import pytest
from fastapi.testclient import TestClient
from tests.fixtures.settings import UNREACHABLE_DATABASE_URL, UNREACHABLE_REDIS_URL

import app.main as main_module
from app.core.config import Settings
from app.main import create_app


def test_root_route_returns_service_entrypoint(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Agent Marketplace Backend",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "ready": "/health/ready",
    }


def test_health_route_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_live_route_returns_ok(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_route_returns_ok(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        pytest.param(
            {"database_url": UNREACHABLE_DATABASE_URL},
            "database unavailable",
            id="database",
        ),
        pytest.param({"redis_url": UNREACHABLE_REDIS_URL}, "redis unavailable", id="redis"),
    ],
)
def test_health_ready_route_returns_service_unavailable_when_a_dependency_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    db_settings: Settings,
    overrides: dict[str, str],
    detail: str,
) -> None:
    settings = db_settings.model_copy(update=overrides)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "/problems/not_ready",
        "status": 503,
        "detail": detail,
    }
