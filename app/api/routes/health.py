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


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Read the combined health check",
    description=(
        "Returns a lightweight application health response. This is suitable for "
        "basic local smoke checks."
    ),
    responses={200: {"description": "Application health check passed."}},
)
def read_health() -> HealthResponse:
    return get_health_response()


@router.get(
    "/health/live",
    response_model=HealthResponse,
    summary="Read the liveness probe",
    description="Confirms the API process is running and able to serve requests.",
    responses={200: {"description": "Application liveness check passed."}},
)
def read_health_live() -> HealthResponse:
    return get_health_response()


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    summary="Read the readiness probe",
    description=(
        "Confirms the API is ready to serve traffic, including critical backing "
        "dependencies such as the database and Redis-backed guardrails when configured."
    ),
    responses={
        200: {"description": "Application readiness check passed."},
        503: {"description": "A required dependency is unavailable."},
    },
)
async def read_health_ready(request: Request) -> HealthResponse | JSONResponse:
    try:
        return await get_readiness_response(get_app_state(request.app))
    except ReadinessCheckError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc)},
        )
