from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.db.session import get_db_session
from app.schemas.service import (
    EndpointCreateRequest,
    EndpointResponse,
    EndpointUpdateRequest,
    EndpointUpstreamRequest,
    ServiceCreateRequest,
    ServiceResponse,
    ServiceTagsUpdateRequest,
    ServiceUpdateRequest,
)
from app.services.provider_draft_service import ProviderDraftService
from app.services.provider_endpoint_service import ProviderEndpointService
from app.services.provider_service_errors import (
    ProviderServiceConflictError,
    ProviderServiceNotFoundError,
    ProviderServiceStateError,
    ProviderServiceValidationError,
)
from app.services.publish_service import PublishService

router = APIRouter(prefix="/provider", tags=["provider-services"])


def _to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ProviderServiceConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ProviderServiceNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ProviderServiceStateError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ProviderServiceValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/services",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider_service(
    request: ServiceCreateRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = ProviderDraftService(session)

    try:
        created = await service.create_service(actor, request)
    except (
        ProviderServiceConflictError,
        ProviderServiceNotFoundError,
        ProviderServiceStateError,
        ProviderServiceValidationError,
    ) as exc:
        raise _to_http_exception(exc) from exc

    return ServiceResponse.from_model(created)


@router.get("/services", response_model=list[ServiceResponse])
async def list_provider_services(
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ServiceResponse]:
    service = ProviderDraftService(session)

    try:
        services = await service.list_services(actor)
    except (
        ProviderServiceConflictError,
        ProviderServiceNotFoundError,
        ProviderServiceStateError,
        ProviderServiceValidationError,
    ) as exc:
        raise _to_http_exception(exc) from exc

    return [ServiceResponse.from_model(item) for item in services]


@router.get("/services/{service_id}", response_model=ServiceResponse)
async def get_provider_service(
    service_id: int,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = ProviderDraftService(session)

    try:
        found = await service.get_service(actor, service_id=service_id)
    except (
        ProviderServiceConflictError,
        ProviderServiceNotFoundError,
        ProviderServiceStateError,
        ProviderServiceValidationError,
    ) as exc:
        raise _to_http_exception(exc) from exc

    return ServiceResponse.from_model(found)


@router.patch("/services/{service_id}", response_model=ServiceResponse)
async def update_provider_service(
    service_id: int,
    request: ServiceUpdateRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = ProviderDraftService(session)

    try:
        updated = await service.update_service(actor, service_id=service_id, request=request)
    except (
        ProviderServiceConflictError,
        ProviderServiceNotFoundError,
        ProviderServiceStateError,
        ProviderServiceValidationError,
    ) as exc:
        raise _to_http_exception(exc) from exc

    return ServiceResponse.from_model(updated)


@router.post("/services/{service_id}/tags", response_model=ServiceResponse)
async def replace_provider_service_tags(
    service_id: int,
    request: ServiceTagsUpdateRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = ProviderDraftService(session)

    try:
        updated = await service.replace_tags(actor, service_id=service_id, request=request)
    except (
        ProviderServiceConflictError,
        ProviderServiceNotFoundError,
        ProviderServiceStateError,
        ProviderServiceValidationError,
    ) as exc:
        raise _to_http_exception(exc) from exc

    return ServiceResponse.from_model(updated)


@router.post("/services/{service_id}/publish", response_model=ServiceResponse)
async def publish_provider_service(
    service_id: int,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = PublishService(session)

    try:
        published = await service.publish_service(actor, service_id=service_id)
    except (
        ProviderServiceConflictError,
        ProviderServiceNotFoundError,
        ProviderServiceStateError,
        ProviderServiceValidationError,
    ) as exc:
        raise _to_http_exception(exc) from exc

    return ServiceResponse.from_model(published)


@router.post(
    "/services/{service_id}/endpoints",
    response_model=EndpointResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider_endpoint(
    service_id: int,
    request: EndpointCreateRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EndpointResponse:
    service = ProviderEndpointService(session)

    try:
        endpoint = await service.create_endpoint(actor, service_id=service_id, request=request)
    except (
        ProviderServiceConflictError,
        ProviderServiceNotFoundError,
        ProviderServiceStateError,
        ProviderServiceValidationError,
    ) as exc:
        raise _to_http_exception(exc) from exc

    return EndpointResponse.from_model(endpoint)


@router.patch("/endpoints/{endpoint_id}", response_model=EndpointResponse)
async def update_provider_endpoint(
    endpoint_id: int,
    request: EndpointUpdateRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EndpointResponse:
    service = ProviderEndpointService(session)

    try:
        endpoint = await service.update_endpoint(
            actor,
            endpoint_id=endpoint_id,
            request=request,
        )
    except (
        ProviderServiceConflictError,
        ProviderServiceNotFoundError,
        ProviderServiceStateError,
        ProviderServiceValidationError,
    ) as exc:
        raise _to_http_exception(exc) from exc

    return EndpointResponse.from_model(endpoint)


@router.put("/endpoints/{endpoint_id}/upstream", status_code=status.HTTP_204_NO_CONTENT)
async def put_provider_endpoint_upstream(
    endpoint_id: int,
    request: EndpointUpstreamRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    service = ProviderEndpointService(session)

    try:
        await service.upsert_upstream(actor, endpoint_id=endpoint_id, request=request)
    except (
        ProviderServiceConflictError,
        ProviderServiceNotFoundError,
        ProviderServiceStateError,
        ProviderServiceValidationError,
    ) as exc:
        raise _to_http_exception(exc) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
