"""Request-scoped access to the gateway collaborators held in application state."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.lifespan import get_app_state
from app.integrations.provider_gateway.client import SupportsRequest
from app.integrations.x402.protocols import (
    SupportsFacilitatorClient,
    SupportsX402ResourceServer,
)


def get_http_client(request: Request) -> SupportsRequest:
    http_client = get_app_state(request.app).http_client
    if http_client is None or not isinstance(http_client, SupportsRequest):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="http client is not initialized",
        )
    return http_client


def get_facilitator_client(request: Request) -> SupportsFacilitatorClient:
    facilitator_client = get_app_state(request.app).facilitator_client
    if facilitator_client is None or not isinstance(facilitator_client, SupportsFacilitatorClient):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="facilitator client is not initialized",
        )
    return facilitator_client


def get_x402_resource_server(request: Request) -> SupportsX402ResourceServer:
    x402_resource_server = get_app_state(request.app).x402_resource_server
    if x402_resource_server is None or not isinstance(
        x402_resource_server,
        SupportsX402ResourceServer,
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="x402 resource server is not initialized",
        )
    return x402_resource_server


HttpClientDep = Annotated[SupportsRequest, Depends(get_http_client)]
FacilitatorClientDep = Annotated[SupportsFacilitatorClient, Depends(get_facilitator_client)]
X402ResourceServerDep = Annotated[SupportsX402ResourceServer, Depends(get_x402_resource_server)]
