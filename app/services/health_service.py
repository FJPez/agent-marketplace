from app.schemas.common import HealthResponse


def get_health_response() -> HealthResponse:
    return HealthResponse(status="ok")
