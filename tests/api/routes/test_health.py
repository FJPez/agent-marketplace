from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.lifespan import get_app_state
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


def test_health_ready_route_returns_service_unavailable_without_redis_when_configured() -> None:
    app = create_app()

    class _HealthySession:
        async def execute(self, statement: object) -> object:
            _ = statement
            return 1

    class _HealthySessionContext:
        async def __aenter__(self) -> _HealthySession:
            return _HealthySession()

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            _ = exc_type, exc, tb

    class _HealthySessionFactory:
        def __call__(self) -> _HealthySessionContext:
            return _HealthySessionContext()

    with TestClient(app) as client:
        state = get_app_state(app)
        state.settings = Settings(
            jwt_secret_key="test-secret-key-with-32-bytes-123",
            redis_url="redis://localhost:6379/0",
        )
        state.db_session_factory = _HealthySessionFactory()  # type: ignore[assignment]
        state.redis_client = None

        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "redis unavailable"}
