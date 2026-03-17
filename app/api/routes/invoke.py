from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.core.enums import AccessMode
from app.core.lifespan import get_app_state
from app.db.session import get_db_session
from app.integrations.payouts import SupportsPayoutExecutor
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
from app.services.payment_service import (
    PaidInvokeSuccess,
    PaymentRequiredChallenge,
    PaymentService,
    SupportsFacilitatorClient,
    SupportsX402ResourceServer,
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


def _get_facilitator_client(request: Request) -> SupportsFacilitatorClient:
    facilitator_client = get_app_state(request.app).facilitator_client
    if facilitator_client is None or not isinstance(facilitator_client, SupportsFacilitatorClient):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="facilitator client is not initialized",
        )
    return facilitator_client


def _get_x402_resource_server(request: Request) -> SupportsX402ResourceServer:
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


def _get_payout_executor(request: Request) -> SupportsPayoutExecutor | None:
    payout_executor = get_app_state(request.app).payout_executor
    if payout_executor is None:
        return None
    if not isinstance(payout_executor, SupportsPayoutExecutor):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="payout executor is not initialized",
        )
    return payout_executor


@router.post("/invoke/{service_id_or_slug}", response_model=InvocationResponse)
async def invoke_service(
    service_id_or_slug: str,
    request: InvokeRequest,
    actor: CurrentActor,
    fastapi_request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> InvocationResponse | JSONResponse:
    invoke_service = InvokeService(session, http_client=_get_http_client(fastapi_request))
    try:
        replayed = await invoke_service.try_successful_replay(
            actor,
            service_id_or_slug=service_id_or_slug,
            endpoint_key=request.endpoint_key,
            payload=request.payload,
            quote_id=request.quote_id,
            idempotency_key=idempotency_key,
        )
        if replayed is not None:
            if replayed.access_mode is AccessMode.PAID:
                payment_service = PaymentService(
                    session,
                    http_client=_get_http_client(fastapi_request),
                    facilitator_client=_get_facilitator_client(fastapi_request),
                    x402_resource_server=_get_x402_resource_server(fastapi_request),
                    settings=get_app_state(fastapi_request.app).settings,
                    payout_executor=_get_payout_executor(fastapi_request),
                )
                for header_name, header_value in (
                    await payment_service._build_success_headers_for_invocation(replayed.id)
                ).items():
                    response.headers[header_name] = header_value
            return InvocationResponse.from_model(replayed)

        resolved = await invoke_service.resolve_target(
            actor,
            service_id_or_slug=service_id_or_slug,
            endpoint_key=request.endpoint_key,
            payload=request.payload,
            quote_id=request.quote_id,
        )
        if resolved.endpoint.access_mode is AccessMode.PAID:
            payment_service = PaymentService(
                session,
                http_client=_get_http_client(fastapi_request),
                facilitator_client=_get_facilitator_client(fastapi_request),
                x402_resource_server=_get_x402_resource_server(fastapi_request),
                settings=get_app_state(fastapi_request.app).settings,
                payout_executor=_get_payout_executor(fastapi_request),
            )
            paid_result = await payment_service.handle_paid_invoke(
                actor,
                resolved=resolved,
                idempotency_key=idempotency_key,
                request_headers=dict(fastapi_request.headers),
            )
            if isinstance(paid_result, PaymentRequiredChallenge):
                return JSONResponse(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    content=paid_result.body,
                    headers=paid_result.headers,
                )
            assert isinstance(paid_result, PaidInvokeSuccess)
            for header_name, header_value in paid_result.response_headers.items():
                response.headers[header_name] = header_value
            invocation = paid_result.invocation
        else:
            invocation = await invoke_service.execute(
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
