from fastapi import APIRouter

from app.schemas.common import HealthResponse
from app.services.health_service import get_health_response

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def read_health() -> HealthResponse:
    return get_health_response()
