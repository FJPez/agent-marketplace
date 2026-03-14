from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.discovery import (
    PublicServiceDetail,
    PublicServiceListItem,
    PublicServicePricingResponse,
    PublicServiceSchemaResponse,
)
from app.services.discovery_service import DiscoveryNotFoundError, DiscoveryService

router = APIRouter(tags=["discovery"])


def _to_http_exception(exc: DiscoveryNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/services", response_model=list[PublicServiceListItem])
async def list_services(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[PublicServiceListItem]:
    service = DiscoveryService(session)
    services = await service.list_services()
    return [PublicServiceListItem.from_model(item) for item in services]


@router.get("/services/{service_id_or_slug}", response_model=PublicServiceDetail)
async def get_service_detail(
    service_id_or_slug: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicServiceDetail:
    service = DiscoveryService(session)
    try:
        found = await service.get_service(service_id_or_slug=service_id_or_slug)
    except DiscoveryNotFoundError as exc:
        raise _to_http_exception(exc) from exc

    return PublicServiceDetail.from_model(found)


@router.get(
    "/services/{service_id_or_slug}/schema",
    response_model=PublicServiceSchemaResponse,
)
async def get_service_schema(
    service_id_or_slug: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicServiceSchemaResponse:
    service = DiscoveryService(session)
    try:
        found = await service.get_service(service_id_or_slug=service_id_or_slug)
    except DiscoveryNotFoundError as exc:
        raise _to_http_exception(exc) from exc

    return PublicServiceSchemaResponse.from_model(found)


@router.get(
    "/services/{service_id_or_slug}/pricing",
    response_model=PublicServicePricingResponse,
)
async def get_service_pricing(
    service_id_or_slug: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicServicePricingResponse:
    service = DiscoveryService(session)
    try:
        found = await service.get_service(service_id_or_slug=service_id_or_slug)
    except DiscoveryNotFoundError as exc:
        raise _to_http_exception(exc) from exc

    return PublicServicePricingResponse.from_model(found)
