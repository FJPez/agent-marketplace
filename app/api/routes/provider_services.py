from typing import Annotated

from fastapi import APIRouter, Body, Depends, Response, status
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
from app.services.publish_service import PublishService

router = APIRouter(prefix="/provider", tags=["provider-services"])


@router.post(
    "/services",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft provider service",
    description=(
        "Creates a new provider-owned draft service. Provider authoring routes accept "
        "generic bearer auth (`Authorization: Bearer <jwt-or-api-key>`)."
    ),
    responses={
        201: {"description": "Draft service created successfully."},
        409: {"description": "The service could not be created in the current state."},
        422: {"description": "The service payload was invalid."},
    },
)
async def create_provider_service(
    request: Annotated[
        ServiceCreateRequest,
        Body(
            openapi_examples={
                "demo-service": {
                    "summary": "Create the demo provider service",
                    "value": {
                        "slug": "demo-agent-service",
                        "name": "Demo Agent Service",
                        "summary": "A provider-owned service for demo purposes.",
                        "description": (
                            "Used for discovery, quote, free invoke, and paid invoke walkthroughs."
                        ),
                    },
                }
            }
        ),
    ],
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = ProviderDraftService(session)
    created = await service.create_service(actor, request)
    return ServiceResponse.from_model(created)


@router.get(
    "/services",
    response_model=list[ServiceResponse],
    summary="List provider-owned services",
    description="Lists the services owned by the authenticated provider account.",
    responses={200: {"description": "Owned services returned successfully."}},
)
async def list_provider_services(
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ServiceResponse]:
    service = ProviderDraftService(session)
    services = await service.list_services(actor)
    return [ServiceResponse.from_model(item) for item in services]


@router.get(
    "/services/{service_id}",
    response_model=ServiceResponse,
    summary="Get provider service detail",
    description="Returns the full provider view of a single owned service.",
    responses={
        200: {"description": "Owned service returned successfully."},
        404: {"description": "The requested service does not exist or is not owned by the actor."},
    },
)
async def get_provider_service(
    service_id: int,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = ProviderDraftService(session)
    found = await service.get_service(actor, service_id=service_id)
    return ServiceResponse.from_model(found)


@router.patch(
    "/services/{service_id}",
    response_model=ServiceResponse,
    summary="Update provider service metadata",
    description=(
        "Updates mutable service metadata for an owned service. Contract-affecting "
        "changes are still subject to the service revision and change-token rules."
    ),
    responses={
        200: {"description": "Service updated successfully."},
        404: {"description": "The requested service does not exist or is not owned by the actor."},
        409: {"description": "The requested service cannot be updated in its current state."},
        422: {"description": "The service update payload was invalid."},
    },
)
async def update_provider_service(
    service_id: int,
    request: Annotated[
        ServiceUpdateRequest,
        Body(
            openapi_examples={
                "rename-service": {
                    "summary": "Update service summary",
                    "value": {
                        "name": "Demo Agent Service",
                        "summary": "Updated service summary for the oral demonstration.",
                        "description": "Optional long-form provider description.",
                    },
                }
            }
        ),
    ],
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = ProviderDraftService(session)
    updated = await service.update_service(actor, service_id=service_id, request=request)
    return ServiceResponse.from_model(updated)


@router.post(
    "/services/{service_id}/tags",
    response_model=ServiceResponse,
    summary="Replace provider service tags",
    description="Replaces the full tag set for an owned provider service.",
    responses={
        200: {"description": "Service tags replaced successfully."},
        404: {"description": "The requested service does not exist or is not owned by the actor."},
        409: {"description": "The requested service cannot be updated in its current state."},
        422: {"description": "The tag list was invalid."},
    },
)
async def replace_provider_service_tags(
    service_id: int,
    request: Annotated[
        ServiceTagsUpdateRequest,
        Body(
            openapi_examples={
                "demo-tags": {
                    "summary": "Replace all provider tags",
                    "value": {"tags": ["demo", "translation"]},
                }
            }
        ),
    ],
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = ProviderDraftService(session)
    updated = await service.replace_tags(actor, service_id=service_id, request=request)
    return ServiceResponse.from_model(updated)


@router.post(
    "/services/{service_id}/publish",
    response_model=ServiceResponse,
    summary="Publish a provider service",
    description=(
        "Publishes an owned service once its endpoints, pricing, upstreams, and health "
        "preconditions are satisfied."
    ),
    responses={
        200: {"description": "Service published successfully."},
        404: {"description": "The requested service does not exist or is not owned by the actor."},
        409: {"description": "The requested service is not ready to publish."},
        422: {"description": "The service configuration is not publishable."},
    },
)
async def publish_provider_service(
    service_id: int,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ServiceResponse:
    service = PublishService(session)
    published = await service.publish_service(actor, service_id=service_id)
    return ServiceResponse.from_model(published)


@router.post(
    "/services/{service_id}/endpoints",
    response_model=EndpointResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a provider endpoint",
    description="Creates a new endpoint under an owned provider service.",
    responses={
        201: {"description": "Endpoint created successfully."},
        404: {"description": "The parent service does not exist or is not owned by the actor."},
        409: {"description": "The endpoint cannot be created in the current service state."},
        422: {"description": "The endpoint payload was invalid."},
    },
)
async def create_provider_endpoint(
    service_id: int,
    request: Annotated[
        EndpointCreateRequest,
        Body(
            openapi_examples={
                "free-endpoint": {
                    "summary": "Create a free endpoint",
                    "value": {
                        "key": "free-ping",
                        "name": "Free Ping",
                        "summary": "Simple free invoke endpoint.",
                        "description": "Echo-style endpoint used in the local mock demo.",
                        "access_mode": "free",
                        "request_schema": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                            "required": ["message"],
                            "additionalProperties": False,
                        },
                        "response_schema": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                            "required": ["message"],
                            "additionalProperties": False,
                        },
                        "timeout_seconds": 30,
                        "is_enabled": True,
                        "pricing": {"pricing_type": "free"},
                    },
                }
            }
        ),
    ],
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EndpointResponse:
    service = ProviderEndpointService(session)
    endpoint = await service.create_endpoint(actor, service_id=service_id, request=request)
    return EndpointResponse.from_model(endpoint)


@router.patch(
    "/endpoints/{endpoint_id}",
    response_model=EndpointResponse,
    summary="Update a provider endpoint",
    description="Updates an owned provider endpoint and its pricing configuration.",
    responses={
        200: {"description": "Endpoint updated successfully."},
        404: {"description": "The requested endpoint does not exist or is not owned by the actor."},
        409: {"description": "The endpoint cannot be updated in the current state."},
        422: {"description": "The endpoint payload was invalid."},
    },
)
async def update_provider_endpoint(
    endpoint_id: int,
    request: Annotated[
        EndpointUpdateRequest,
        Body(
            openapi_examples={
                "paid-endpoint-pricing": {
                    "summary": "Update endpoint pricing",
                    "value": {
                        "summary": "A paid endpoint that returns a compact summary.",
                        "timeout_seconds": 45,
                        "pricing": {
                            "pricing_type": "fixed_per_call",
                            "amount_minor": 250,
                            "currency": "USD",
                        },
                    },
                }
            }
        ),
    ],
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EndpointResponse:
    service = ProviderEndpointService(session)
    endpoint = await service.update_endpoint(
        actor,
        endpoint_id=endpoint_id,
        request=request,
    )
    return EndpointResponse.from_model(endpoint)


@router.put(
    "/endpoints/{endpoint_id}/upstream",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Upsert provider upstream configuration",
    description=(
        "Creates or replaces the hidden upstream configuration for an owned endpoint. "
        "Upstream details are stored privately and are never exposed on public discovery routes."
    ),
    responses={
        204: {"description": "Endpoint upstream configuration stored successfully."},
        404: {"description": "The requested endpoint does not exist or is not owned by the actor."},
        409: {"description": "The upstream cannot be updated in the current state."},
        422: {"description": "The upstream payload was invalid."},
    },
)
async def put_provider_endpoint_upstream(
    endpoint_id: int,
    request: Annotated[
        EndpointUpstreamRequest,
        Body(
            openapi_examples={
                "mock-upstream": {
                    "summary": "Point the endpoint at the local mock upstream",
                    "value": {
                        "base_url": "http://127.0.0.1:9000",
                        "path": "/free-ping",
                        "http_method": "POST",
                        "config": {
                            "auth": {
                                "type": "hmac_sha256",
                                "key_id": "demo-key",
                                "secret": "demo-secret",
                            }
                        },
                    },
                }
            }
        ),
    ],
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    service = ProviderEndpointService(session)
    await service.upsert_upstream(actor, endpoint_id=endpoint_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
