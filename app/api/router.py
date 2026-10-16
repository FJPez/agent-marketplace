from fastapi import APIRouter, Depends

from app.api.deps.rate_limit import enforce_rate_limit
from app.api.routes.account import router as account_router
from app.api.routes.account_wallet import router as account_wallet_router
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.discovery import router as discovery_router
from app.api.routes.health import router as health_router
from app.api.routes.payout_addresses import router as payout_addresses_router
from app.api.routes.provider_services import router as provider_services_router
from app.api.routes.provider_trust import router as provider_trust_router

v1_router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])
v1_router.include_router(admin_router)
v1_router.include_router(auth_router)
v1_router.include_router(account_router)
v1_router.include_router(account_wallet_router)
v1_router.include_router(provider_services_router)
v1_router.include_router(provider_trust_router)
v1_router.include_router(payout_addresses_router)
v1_router.include_router(discovery_router)

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(v1_router)
