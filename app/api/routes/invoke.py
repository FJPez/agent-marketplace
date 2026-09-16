from typing import Annotated

from fastapi import APIRouter, Body, Response, status
from fastapi.responses import JSONResponse

from app.api.deps.auth import CurrentActor
from app.api.deps.database import SessionDep
from app.api.deps.gateway import FacilitatorClientDep, HttpClientDep, X402ResourceServerDep
from app.api.deps.headers import PaymentSignatureHeader, ValidatedIdempotencyKey
from app.api.deps.settings import SettingsDep
from app.schemas.invoke import InvocationListItem, InvocationResponse, InvokeRequest
from app.schemas.service_ref import PublicServiceRef
from app.services import invoke, invoke_submission
from app.services.payment_service import PaymentRequiredChallenge

router = APIRouter(tags=["invoke"])


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
async def invoke_endpoint(
    service_id_or_slug: PublicServiceRef,
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
    response: Response,
    session: SessionDep,
    idempotency_key: ValidatedIdempotencyKey,
    http_client: HttpClientDep,
    facilitator_client: FacilitatorClientDep,
    x402_resource_server: X402ResourceServerDep,
    settings: SettingsDep,
    payment_signature: PaymentSignatureHeader = None,
) -> InvocationResponse | JSONResponse:
    outcome = await invoke_submission.submit(
        session=session,
        actor=actor,
        service_ref=service_id_or_slug,
        request=request,
        idempotency_key=idempotency_key,
        payment_signature=payment_signature,
        http_client=http_client,
        facilitator_client=facilitator_client,
        x402_resource_server=x402_resource_server,
        settings=settings,
    )
    if isinstance(outcome, PaymentRequiredChallenge):
        return JSONResponse(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            content=outcome.body,
            headers=outcome.headers,
        )
    for header_name, header_value in outcome.response_headers.items():
        response.headers[header_name] = header_value
    return InvocationResponse.from_model(outcome.invocation)


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
    session: SessionDep,
) -> InvocationResponse:
    invocation = await invoke.get_invocation(
        session=session,
        account_id=actor.account_id,
        invocation_id=invocation_id,
    )
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
    session: SessionDep,
) -> list[InvocationListItem]:
    invocations = await invoke.list_invocations(session=session, account_id=actor.account_id)
    return [InvocationListItem.from_model(item) for item in invocations]
