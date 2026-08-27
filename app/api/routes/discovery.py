from fastapi import APIRouter

from app.api.deps.database import SessionDep
from app.schemas.discovery import (
    PublicServiceDetail,
    PublicServiceListItem,
    PublicServicePricingResponse,
    PublicServiceSchemaResponse,
)
from app.schemas.service_ref import PublicServiceRef
from app.services import discovery

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
async def list_services(session: SessionDep) -> list[PublicServiceListItem]:
    services = await discovery.list_services(session=session)
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
        422: {"description": "The supplied identifier was neither a service id nor a slug."},
    },
)
async def get_service_detail(
    service_id_or_slug: PublicServiceRef,
    session: SessionDep,
) -> PublicServiceDetail:
    found = await discovery.get_service(session=session, service_ref=service_id_or_slug)
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
        422: {"description": "The supplied identifier was neither a service id nor a slug."},
    },
)
async def get_service_schema(
    service_id_or_slug: PublicServiceRef,
    session: SessionDep,
) -> PublicServiceSchemaResponse:
    found = await discovery.get_service(session=session, service_ref=service_id_or_slug)
    return PublicServiceSchemaResponse.from_model(found)


@router.get(
    "/services/{service_id_or_slug}/pricing",
    response_model=PublicServicePricingResponse,
    summary="Get public service pricing",
    description=("Returns the public pricing information for each enabled endpoint on a service."),
    responses={
        200: {"description": "Public service pricing returned successfully."},
        404: {"description": "No public service matched the supplied identifier."},
        422: {"description": "The supplied identifier was neither a service id nor a slug."},
    },
)
async def get_service_pricing(
    service_id_or_slug: PublicServiceRef,
    session: SessionDep,
) -> PublicServicePricingResponse:
    found = await discovery.get_service(session=session, service_ref=service_id_or_slug)
    return PublicServicePricingResponse.from_model(found)
