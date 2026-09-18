"""Orchestration of one submitted invoke: replay, resolve, then run it free or paid."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.enums import AccessMode, InvocationStatus
from app.db.models import Invocation
from app.integrations.provider_gateway.client import SupportsRequest
from app.integrations.x402.protocols import (
    SupportsFacilitatorClient,
    SupportsX402ResourceServer,
)
from app.schemas.invoke import InvokeRequest
from app.schemas.service_ref import PublicServiceRef
from app.services import invoke, payment
from app.services.payment import PaymentRequiredChallenge


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
        if replayed.access_mode is AccessMode.PAID:
            # A paid replay owes the payer bookkeeping either way, so a stored failure is
            # raised by the payment flow once the compensation it requires is durable.
            finished = await payment.finish_replayed_invocation(
                session=session,
                actor=actor,
                invocation=replayed,
                x402_resource_server=x402_resource_server,
                settings=settings,
            )
            return InvokeSuccess(
                invocation=finished.invocation,
                response_headers=finished.response_headers,
            )
        if replayed.status is InvocationStatus.FAILED:
            raise invoke.exception_for_failed_invocation(replayed)
        return InvokeSuccess(invocation=replayed, response_headers={})

    resolved = await invoke.resolve_target(
        session=session,
        service_ref=service_ref,
        endpoint_key=request.endpoint_key,
        payload=request.payload,
        quote_id=request.quote_id,
    )
    if resolved.endpoint.access_mode is AccessMode.PAID:
        paid_result = await payment.handle_paid_invoke(
            session=session,
            actor=actor,
            resolved=resolved,
            idempotency_key=idempotency_key,
            payment_signature=payment_signature,
            facilitator_client=facilitator_client,
            x402_resource_server=x402_resource_server,
            http_client=http_client,
            settings=settings,
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
