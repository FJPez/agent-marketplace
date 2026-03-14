from fastapi import APIRouter

from app.api.routes.consumers import router as consumers_router
from app.api.routes.discovery import router as discovery_router
from app.api.routes.health import router as health_router
from app.api.routes.provider_services import router as provider_services_router
from app.api.routes.providers import router as providers_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(providers_router, prefix="/v1")
api_router.include_router(consumers_router, prefix="/v1")
api_router.include_router(provider_services_router, prefix="/v1")
api_router.include_router(discovery_router, prefix="/v1")
