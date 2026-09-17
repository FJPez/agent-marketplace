"""Orchestration of one submitted invoke: replay, resolve, then run it free or paid."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.enums import AccessMode, InvocationStatus
from app.db.models import Invocation
from app.integrations.provider_gateway.client import SupportsRequest
from app.schemas.invoke import InvokeRequest
from app.schemas.service_ref import PublicServiceRef
from app.services import invoke
from app.services.payment_service import (
    PaymentRequiredChallenge,
    PaymentService,
    SupportsFacilitatorClient,
    SupportsX402ResourceServer,
)


@dataclass(frozen=True, slots=True)
class InvokeSuccess:
    invocation: Invocation
    response_headers: dict[str, str]


async def submit(
    *,
    session: AsyncSession,
    actor: ActorContext,
    service_ref: PublicServiceRef,
    request: InvokeRequest,
    idempotency_key: str,
    payment_signature: str | None,
    http_client: SupportsRequest,
    facilitator_client: SupportsFacilitatorClient,
    x402_resource_server: SupportsX402ResourceServer,
    settings: Settings,
) -> InvokeSuccess | PaymentRequiredChallenge:
    payments = PaymentService(
        session,
        http_client=http_client,
        facilitator_client=facilitator_client,
        x402_resource_server=x402_resource_server,
        settings=settings,
    )

    replayed = await invoke.try_replay(
        session=session,
        account_id=actor.account_id,
        service_ref=service_ref,
        endpoint_key=request.endpoint_key,
        payload=request.payload,
        quote_id=request.quote_id,
        idempotency_key=idempotency_key,
    )
    if replayed is not None:
        # A stored failure settles the repeated request whatever its access mode, and
        # settles it before any payment header is looked up.
        if replayed.status is InvocationStatus.FAILED:
            raise invoke.exception_for_failed_invocation(replayed)
        if replayed.access_mode is not AccessMode.PAID:
            return InvokeSuccess(invocation=replayed, response_headers={})
        replay_headers = await payments.build_success_headers_for_invocation(replayed.id)
        # A paid invoke without settled payment headers is not replayable as a whole.
        if replay_headers:
            return InvokeSuccess(invocation=replayed, response_headers=replay_headers)

    resolved = await invoke.resolve_target(
        session=session,
        service_ref=service_ref,
        endpoint_key=request.endpoint_key,
        payload=request.payload,
        quote_id=request.quote_id,
    )
    if resolved.endpoint.access_mode is AccessMode.PAID:
        paid_result = await payments.handle_paid_invoke(
            actor,
            resolved=resolved,
            idempotency_key=idempotency_key,
            payment_signature=payment_signature,
        )
        if isinstance(paid_result, PaymentRequiredChallenge):
            return paid_result
        return InvokeSuccess(
            invocation=paid_result.invocation,
            response_headers=paid_result.response_headers,
        )

    invocation = await invoke.execute(
        session=session,
        account_id=actor.account_id,
        resolved=resolved,
        idempotency_key=idempotency_key,
        http_client=http_client,
    )
    if invocation.status is InvocationStatus.FAILED:
        raise invoke.exception_for_failed_invocation(invocation)
    return InvokeSuccess(invocation=invocation, response_headers={})
