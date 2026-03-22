from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.api.deps.headers import ValidatedIdempotencyKey
from app.core.enums import AccessMode
from app.core.lifespan import get_app_state
from app.db.session import get_db_session
from app.integrations.provider_gateway.client import SupportsRequest
from app.schemas.invoke import InvocationListItem, InvocationResponse, InvokeRequest
from app.services.invoke_service import InvokeService
from app.services.payment_service import (
    PaidInvokeSuccess,
    PaymentRequiredChallenge,
    PaymentService,
    SupportsFacilitatorClient,
    SupportsX402ResourceServer,
)

router = APIRouter(tags=["invoke"])


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


@router.post(
    "/invoke/{service_id_or_slug}",
    response_model=InvocationResponse,
    summary="Invoke a service endpoint",
    description=(
        "Invokes a service endpoint on behalf of the authenticated actor. Every request "
        "must include an `Idempotency-Key` header. Free invokes execute immediately; paid "
        "invokes may first return `402 Payment Required` with `PAYMENT-REQUIRED` metadata "
        "and must be retried with payment headers."
    ),
    responses={
        200: {"description": "Invocation completed successfully."},
        402: {
            "description": "Payment is required before the invoke can proceed.",
            "headers": {
                "PAYMENT-REQUIRED": {
                    "description": (
                        "Serialized x402 payment requirement for the requested paid invoke."
                    )
                },
                "X-Request-ID": {
                    "description": "Request correlation identifier echoed by the API."
                },
            },
        },
        404: {"description": "The requested service or endpoint could not be resolved."},
        409: {
            "description": (
                "The invoke could not proceed because of a quote, state, or idempotency conflict."
            )
        },
        502: {"description": "The provider upstream returned an invalid response."},
        504: {
            "description": "The provider upstream did not respond before the configured timeout."
        },
    },
)
async def invoke_service(
    service_id_or_slug: str,
    request: Annotated[
        InvokeRequest,
        Body(
            openapi_examples={
                "free-invoke": {
                    "summary": "Invoke a free endpoint",
                    "value": {
                        "endpoint_key": "free-ping",
                        "payload": {"message": "hello from the local demo"},
                        "quote_id": None,
                    },
                },
                "paid-invoke": {
                    "summary": "Invoke a paid endpoint after creating a quote",
                    "value": {
                        "endpoint_key": "paid-summary",
                        "payload": {"message": "Please summarize this paid request."},
                        "quote_id": 1,
                    },
                },
            }
        ),
    ],
    actor: CurrentActor,
    fastapi_request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: ValidatedIdempotencyKey,
) -> InvocationResponse | JSONResponse:
    invoke_service = InvokeService(session, http_client=_get_http_client(fastapi_request))
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
            )
            replay_headers = await payment_service.build_success_headers_for_invocation(replayed.id)
            if not replay_headers:
                replayed = None
            else:
                for header_name, header_value in replay_headers.items():
                    response.headers[header_name] = header_value
        if replayed is not None:
            if replayed.access_mode is not AccessMode.PAID:
                return InvocationResponse.from_model(replayed)
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
    return InvocationResponse.from_model(invocation)


@router.get(
    "/invocations/{invocation_id}",
    response_model=InvocationResponse,
    summary="Get invocation detail",
    description="Returns a single invocation record owned by the authenticated actor.",
    responses={
        200: {"description": "Invocation returned successfully."},
        404: {"description": "The requested invocation does not exist."},
    },
)
async def get_invocation(
    invocation_id: int,
    actor: CurrentActor,
    fastapi_request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InvocationResponse:
    service = InvokeService(session, http_client=_get_http_client(fastapi_request))
    invocation = await service.get_invocation(actor, invocation_id=invocation_id)
    return InvocationResponse.from_model(invocation)


@router.get(
    "/invocations",
    response_model=list[InvocationListItem],
    summary="List invocations",
    description="Lists invocation records owned by the authenticated actor.",
    responses={200: {"description": "Invocation list returned successfully."}},
)
async def list_invocations(
    actor: CurrentActor,
    fastapi_request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[InvocationListItem]:
    service = InvokeService(session, http_client=_get_http_client(fastapi_request))
    invocations = await service.list_invocations(actor)
    return [InvocationListItem.from_model(item) for item in invocations]
