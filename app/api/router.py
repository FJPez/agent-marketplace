from fastapi import APIRouter

from app.api.routes.account import router as account_router
from app.api.routes.account_wallet import router as account_wallet_router
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.discovery import router as discovery_router
from app.api.routes.finance import router as finance_router
from app.api.routes.health import router as health_router
from app.api.routes.invoke import router as invoke_router
from app.api.routes.provider_services import router as provider_services_router
from app.api.routes.quotes import router as quotes_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(admin_router, prefix="/v1")
api_router.include_router(auth_router, prefix="/v1")
api_router.include_router(account_router, prefix="/v1")
api_router.include_router(account_wallet_router, prefix="/v1")
api_router.include_router(provider_services_router, prefix="/v1")
api_router.include_router(finance_router, prefix="/v1")
api_router.include_router(discovery_router, prefix="/v1")
api_router.include_router(quotes_router, prefix="/v1")
api_router.include_router(invoke_router, prefix="/v1")
