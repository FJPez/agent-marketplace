from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.discovery import (
    PublicServiceDetail,
    PublicServiceListItem,
    PublicServicePricingResponse,
    PublicServiceSchemaResponse,
)
from app.services.discovery_service import DiscoveryService

router = APIRouter(tags=["discovery"])


@router.get(
    "/services",
    response_model=list[PublicServiceListItem],
    summary="List public services",
    description=(
        "Returns the public discovery catalogue of active, non-delisted, non-suspended services."
    ),
    responses={200: {"description": "Public service list returned successfully."}},
)
async def list_services(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[PublicServiceListItem]:
    service = DiscoveryService(session)
    services = await service.list_services()
    return [PublicServiceListItem.from_model(item) for item in services]


@router.get(
    "/services/{service_id_or_slug}",
    response_model=PublicServiceDetail,
    summary="Get public service detail",
    description=(
        "Returns the public detail view for a service, including enabled endpoint summaries only."
    ),
    responses={
        200: {"description": "Public service detail returned successfully."},
        404: {"description": "No public service matched the supplied identifier."},
    },
)
async def get_service_detail(
    service_id_or_slug: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicServiceDetail:
    service = DiscoveryService(session)
    found = await service.get_service(service_id_or_slug=service_id_or_slug)
    return PublicServiceDetail.from_model(found)


@router.get(
    "/services/{service_id_or_slug}/schema",
    response_model=PublicServiceSchemaResponse,
    summary="Get public service schemas",
    description=(
        "Returns the public request and response JSON schemas for each enabled endpoint "
        "on a service."
    ),
    responses={
        200: {"description": "Public service schemas returned successfully."},
        404: {"description": "No public service matched the supplied identifier."},
    },
)
async def get_service_schema(
    service_id_or_slug: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicServiceSchemaResponse:
    service = DiscoveryService(session)
    found = await service.get_service(service_id_or_slug=service_id_or_slug)
    return PublicServiceSchemaResponse.from_model(found)


@router.get(
    "/services/{service_id_or_slug}/pricing",
    response_model=PublicServicePricingResponse,
    summary="Get public service pricing",
    description=("Returns the public pricing information for each enabled endpoint on a service."),
    responses={
        200: {"description": "Public service pricing returned successfully."},
        404: {"description": "No public service matched the supplied identifier."},
    },
)
async def get_service_pricing(
    service_id_or_slug: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicServicePricingResponse:
    service = DiscoveryService(session)
    found = await service.get_service(service_id_or_slug=service_id_or_slug)
    return PublicServicePricingResponse.from_model(found)
