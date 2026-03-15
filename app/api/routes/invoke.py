from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.core.enums import AccessMode
from app.core.lifespan import get_app_state
from app.db.session import get_db_session
from app.integrations.provider_gateway.client import SupportsRequest
from app.schemas.invoke import InvocationListItem, InvocationResponse, InvokeRequest
from app.services.invoke_service import (
    InvokeBadGatewayError,
    InvokeConflictError,
    InvokeGatewayTimeoutError,
    InvokeNotFoundError,
    InvokeService,
    InvokeUnavailableError,
)

router = APIRouter(tags=["invoke"])


def _to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, InvokeNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, (InvokeConflictError, InvokeUnavailableError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, InvokeGatewayTimeoutError):
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    if isinstance(exc, InvokeBadGatewayError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


def _get_http_client(request: Request) -> SupportsRequest:
    http_client = get_app_state(request.app).http_client
    if http_client is None or not isinstance(http_client, SupportsRequest):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="http client is not initialized",
        )
    return http_client


@router.post("/invoke/{service_id_or_slug}", response_model=InvocationResponse)
async def invoke_service(
    service_id_or_slug: str,
    request: InvokeRequest,
    actor: CurrentActor,
    fastapi_request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> InvocationResponse:
    service = InvokeService(session, http_client=_get_http_client(fastapi_request))
    try:
        resolved = await service.resolve_target(
            actor,
            service_id_or_slug=service_id_or_slug,
            endpoint_key=request.endpoint_key,
            payload=request.payload,
            quote_id=request.quote_id,
        )
        if resolved.endpoint.access_mode is AccessMode.PAID:
            raise InvokeConflictError("paid invoke requires x402 payment flow")
        invocation = await service.execute(
            actor,
            resolved=resolved,
            idempotency_key=idempotency_key,
        )
    except (
        InvokeBadGatewayError,
        InvokeConflictError,
        InvokeGatewayTimeoutError,
        InvokeNotFoundError,
        InvokeUnavailableError,
    ) as exc:
        raise _to_http_exception(exc) from exc
    return InvocationResponse.from_model(invocation)


@router.get("/invocations/{invocation_id}", response_model=InvocationResponse)
async def get_invocation(
    invocation_id: int,
    actor: CurrentActor,
    fastapi_request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InvocationResponse:
    service = InvokeService(session, http_client=_get_http_client(fastapi_request))
    try:
        invocation = await service.get_invocation(actor, invocation_id=invocation_id)
    except InvokeNotFoundError as exc:
        raise _to_http_exception(exc) from exc
    return InvocationResponse.from_model(invocation)


@router.get("/invocations", response_model=list[InvocationListItem])
async def list_invocations(
    actor: CurrentActor,
    fastapi_request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[InvocationListItem]:
    service = InvokeService(session, http_client=_get_http_client(fastapi_request))
    try:
        invocations = await service.list_invocations(actor)
    except InvokeNotFoundError as exc:
        raise _to_http_exception(exc) from exc
    return [InvocationListItem.from_model(item) for item in invocations]
