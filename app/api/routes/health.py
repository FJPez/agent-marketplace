from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.lifespan import get_app_state
from app.schemas.common import HealthResponse
from app.services.health_service import (
    ReadinessCheckError,
    get_health_response,
    get_readiness_response,
)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def read_health() -> HealthResponse:
    return get_health_response()


@router.get("/health/live", response_model=HealthResponse)
def read_health_live() -> HealthResponse:
    return get_health_response()


@router.get("/health/ready", response_model=HealthResponse)
async def read_health_ready(request: Request) -> HealthResponse | JSONResponse:
    try:
        return await get_readiness_response(get_app_state(request.app))
    except ReadinessCheckError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc)},
        )
