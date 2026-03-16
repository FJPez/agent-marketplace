from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sqlalchemy.exc import IntegrityError

from app.core.enums import PricingModelType
from app.integrations.x402.facilitator_client import (
    FacilitatorAuthError,
    FacilitatorConfigError,
    FacilitatorUnavailableError,
)
from app.integrations.x402.payment_identifier import (
    InvalidPaymentPayloadError,
    extract_payment_identifier,
    parse_payment_header,
)
from app.integrations.x402.payment_requirements import (
    PaymentRequirementConfigError,
    build_payment_requirement,
)
from app.repositories.payment_attempt_repo import PaymentAttemptRepository
from app.services.invoke_service import (
    InvokeBadGatewayError,
    InvokeConflictError,
    InvokeGatewayTimeoutError,
    InvokeService,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.core.config import Settings
    from app.db.models import Invocation
    from app.integrations.provider_gateway.client import SupportsRequest
    from app.services.invoke_service import ResolvedInvokeTarget


@runtime_checkable
class SupportsFacilitatorClient(Protocol):
    async def verify(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]: ...

    async def settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]: ...


@runtime_checkable
class SupportsX402ResourceServer(Protocol):
    def build_payment_required_headers(
        self,
        *,
        payment_requirement: dict[str, object],
    ) -> dict[str, str]: ...

    def build_payment_response_headers(
        self,
        *,
        settle_outcome: dict[str, object],
    ) -> dict[str, str]: ...


@dataclass(frozen=True, slots=True)
class PaymentRequiredChallenge:
    headers: dict[str, str]
    body: dict[str, str]


@dataclass(frozen=True, slots=True)
class PaidInvokeSuccess:
    invocation: Invocation
    response_headers: dict[str, str]


class PaymentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        http_client: SupportsRequest,
        facilitator_client: SupportsFacilitatorClient,
        x402_resource_server: SupportsX402ResourceServer,
        settings: Settings,
    ) -> None:
        self._session = session
        self._facilitator_client = facilitator_client
        self._x402_resource_server = x402_resource_server
        self._settings = settings
        self._attempt_repo = PaymentAttemptRepository(session)
        self._invoke_service = InvokeService(session, http_client=http_client)

    async def handle_paid_invoke(
        self,
        actor: ActorContext,
        *,
        resolved: ResolvedInvokeTarget,
        idempotency_key: str,
        request_headers: dict[str, str],
    ) -> PaymentRequiredChallenge | PaidInvokeSuccess:
        existing = await self._invoke_service.get_replayable_invocation(
            actor,
            idempotency_key=idempotency_key,
            request_hash=resolved.request_hash,
        )
        if existing is not None:
            return PaidInvokeSuccess(
                invocation=existing,
                response_headers=await self._build_success_headers_for_invocation(existing.id),
            )

        if resolved.quote is None:
            raise InvokeConflictError("paid invoke requires quote")
        if (
            resolved.quote.pricing_type is not PricingModelType.FIXED_PER_CALL
            or resolved.quote.amount_minor is None
        ):
            raise InvokeConflictError("payment currency is not supported")

        payment_requirement = self._build_requirement(
            amount_minor=resolved.quote.amount_minor,
            currency=resolved.quote.currency,
        )
        payment_header = request_headers.get("PAYMENT-SIGNATURE") or request_headers.get(
            "payment-signature"
        )
        if payment_header is None:
            return self._challenge(payment_requirement, detail="payment required")

        try:
            payment_payload = parse_payment_header(payment_header)
            payment_identifier = extract_payment_identifier(payment_payload)
        except InvalidPaymentPayloadError:
            return self._challenge(payment_requirement, detail="payment required")

        attempt = None
        try:
            async with self._session.begin_nested():
                attempt = self._attempt_repo.add(
                    consumer_account_id=actor.account_id,
                    quote_id=resolved.quote.id,
                    invocation_id=None,
                    idempotency_key=idempotency_key,
                    payment_identifier=payment_identifier,
                    payment_requirement=payment_requirement,
                    payment_payload=payment_payload,
                    verify_outcome=None,
                    settle_outcome=None,
                    facilitator_reference=None,
                )
                await self._session.flush()
        except IntegrityError:
            existing_attempt = await self._attempt_repo.get_by_payment_identifier(
                payment_identifier=payment_identifier,
            )
            if existing_attempt is None or existing_attempt.quote_id != resolved.quote.id:
                raise InvokeConflictError("payment identifier already used") from None
            if existing_attempt.invocation_id is None:
                raise InvokeConflictError("payment identifier already used") from None
            invocation = await self._invoke_service.get_invocation(
                actor,
                invocation_id=existing_attempt.invocation_id,
            )
            return PaidInvokeSuccess(
                invocation=invocation,
                response_headers=(
                    self._build_response_headers(existing_attempt.settle_outcome)
                    if existing_attempt.settle_outcome
                    else {}
                ),
            )
        assert attempt is not None

        verify_outcome = await self._verify(
            payment_requirement=payment_requirement,
            payment_payload=payment_payload,
        )
        attempt.verify_outcome = verify_outcome
        attempt.facilitator_reference = _extract_reference(verify_outcome)
        if not _is_verify_success(verify_outcome):
            await self._session.commit()
            return self._challenge(payment_requirement, detail="payment could not be verified")

        settle_outcome = await self._settle(
            payment_requirement=payment_requirement,
            payment_payload=payment_payload,
        )
        attempt.settle_outcome = settle_outcome
        attempt.facilitator_reference = _extract_reference(settle_outcome) or _extract_reference(
            verify_outcome,
        )
        if not _is_settle_success(settle_outcome):
            await self._session.commit()
            raise InvokeBadGatewayError("payment settlement failed")

        try:
            invocation = await self._invoke_service.execute(
                actor,
                resolved=resolved,
                idempotency_key=idempotency_key,
                auto_commit=False,
            )
        except (
            InvokeBadGatewayError,
            InvokeConflictError,
            InvokeGatewayTimeoutError,
        ):
            await self._session.commit()
            raise
        attempt.invocation_id = invocation.id
        await self._session.commit()
        return PaidInvokeSuccess(
            invocation=invocation,
            response_headers=self._build_response_headers(settle_outcome),
        )

    def _build_requirement(
        self,
        *,
        amount_minor: int,
        currency: str | None,
    ) -> dict[str, object]:
        try:
            return build_payment_requirement(
                amount_minor=amount_minor,
                currency=currency,
                pay_to_address=self._settings.x402_pay_to_address,
                facilitator_url=self._settings.x402_facilitator_url,
                network=self._settings.x402_network,
                network_caip2=self._settings.x402_network_caip2,
            )
        except PaymentRequirementConfigError as exc:
            raise InvokeConflictError(str(exc)) from exc

    def _challenge(
        self,
        payment_requirement: dict[str, object],
        *,
        detail: str,
    ) -> PaymentRequiredChallenge:
        headers = self._x402_resource_server.build_payment_required_headers(
            payment_requirement=payment_requirement,
        )
        return PaymentRequiredChallenge(headers=headers, body={"detail": detail})

    def _build_response_headers(self, settle_outcome: dict[str, object]) -> dict[str, str]:
        return self._x402_resource_server.build_payment_response_headers(
            settle_outcome=settle_outcome,
        )

    async def _build_success_headers_for_invocation(self, invocation_id: int) -> dict[str, str]:
        attempt = await self._attempt_repo.get_by_invocation_id(invocation_id=invocation_id)
        if attempt is None or attempt.settle_outcome is None:
            return {}
        return self._build_response_headers(attempt.settle_outcome)

    async def _verify(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        try:
            return await self._facilitator_client.verify(
                payment_requirement=payment_requirement,
                payment_payload=payment_payload,
            )
        except FacilitatorConfigError as exc:
            raise InvokeBadGatewayError(str(exc)) from exc
        except FacilitatorAuthError as exc:
            raise InvokeBadGatewayError("facilitator authentication failed") from exc
        except FacilitatorUnavailableError as exc:
            raise InvokeBadGatewayError(str(exc)) from exc

    async def _settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        try:
            return await self._facilitator_client.settle(
                payment_requirement=payment_requirement,
                payment_payload=payment_payload,
            )
        except FacilitatorConfigError as exc:
            raise InvokeBadGatewayError(str(exc)) from exc
        except FacilitatorAuthError as exc:
            raise InvokeBadGatewayError("facilitator authentication failed") from exc
        except FacilitatorUnavailableError as exc:
            raise InvokeBadGatewayError(str(exc)) from exc


def _is_verify_success(verify_outcome: dict[str, object]) -> bool:
    return bool(
        verify_outcome.get("isValid") or verify_outcome.get("ok") or verify_outcome.get("is_valid")
    )


def _is_settle_success(settle_outcome: dict[str, object]) -> bool:
    return bool(settle_outcome.get("ok") or settle_outcome.get("success"))


def _extract_reference(outcome: dict[str, object]) -> str | None:
    for key in ("reference", "transaction"):
        value = outcome.get(key)
        if isinstance(value, str) and value:
            return value
    return None
