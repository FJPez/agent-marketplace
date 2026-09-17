from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from app.core.enums import InvocationStatus, PaymentAttemptStatus, PricingModelType
from app.core.errors import ConflictError, UpstreamError
from app.core.logging import (
    INVOCATION_ID_FIELD,
    PAYMENT_ATTEMPT_ID_FIELD,
    PROVIDER_ACCOUNT_ID_FIELD,
    QUOTE_ID_FIELD,
    SERVICE_ID_FIELD,
    build_event_context,
    get_logger,
)
from app.integrations.x402.facilitator_client import FacilitatorAuthError, FacilitatorError
from app.integrations.x402.models import (
    InvalidPaymentPayloadError,
    PaymentPayload,
    PaymentRequirement,
    SettleOutcome,
    VerifyOutcome,
)
from app.integrations.x402.payment_requirements import (
    PaymentRequirementConfigError,
    build_payment_requirement,
)
from app.integrations.x402.protocols import (
    SupportsFacilitatorClient,
    SupportsX402ResourceServer,
)
from app.repositories.payment_attempt_repo import PaymentAttemptRepository
from app.services import invoke
from app.services.ledger_service import LedgerService
from app.services.payout_service import PayoutExecutionService

logger = get_logger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.core.config import Settings
    from app.db.models import Invocation, PaymentAttempt
    from app.integrations.provider_gateway.client import SupportsRequest
    from app.services.invoke import ResolvedInvokeTarget


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
        self._http_client = http_client
        self._ledger_service = LedgerService(session)

    async def handle_paid_invoke(
        self,
        actor: ActorContext,
        *,
        resolved: ResolvedInvokeTarget,
        idempotency_key: str,
        payment_signature: str | None,
    ) -> PaymentRequiredChallenge | PaidInvokeSuccess:
        quote = resolved.quote
        if quote is None:
            raise ConflictError("paid invoke requires quote")
        quote_id = quote.id
        service_id = resolved.service.id
        endpoint_key = resolved.endpoint.key
        payload = resolved.payload
        if quote.pricing_type is not PricingModelType.FIXED_PER_CALL or quote.amount_minor is None:
            raise ConflictError("payment currency is not supported")

        payment_requirement = self._build_requirement(
            amount_minor=quote.amount_minor,
            currency=quote.currency,
        )
        if payment_signature is None:
            return self._challenge(payment_requirement, detail="payment required")

        try:
            payment_payload = PaymentPayload.from_header(payment_signature)
        except InvalidPaymentPayloadError:
            return self._challenge(payment_requirement, detail="payment required")

        attempt, target_needs_reload = await self._get_or_create_attempt(
            actor,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            payment_requirement=payment_requirement,
            payment_payload=payment_payload,
        )
        if attempt.quote_id != quote_id:
            raise ConflictError("payment identifier already used")
        if target_needs_reload:
            resolved = await invoke.resolve_target(
                session=self._session,
                service_ref=service_id,
                endpoint_key=endpoint_key,
                payload=payload,
                quote_id=quote_id,
            )
        return await self._resume_attempt(
            actor,
            resolved=resolved,
            payment_requirement=payment_requirement,
            payment_payload=payment_payload,
            attempt=attempt,
        )

    async def _resume_attempt(
        self,
        actor: ActorContext,
        *,
        resolved: ResolvedInvokeTarget,
        payment_requirement: PaymentRequirement,
        payment_payload: PaymentPayload,
        attempt: PaymentAttempt,
    ) -> PaymentRequiredChallenge | PaidInvokeSuccess:
        quote = resolved.quote
        assert quote is not None
        assert quote.amount_minor is not None
        if attempt.status is PaymentAttemptStatus.CONSUMED and attempt.invocation_id is not None:
            invocation = await invoke.get_invocation(
                session=self._session,
                account_id=actor.account_id,
                invocation_id=attempt.invocation_id,
            )
            logger.info(
                "paid invoke replayed",
                extra=build_event_context(
                    "payment.replayed",
                    **{
                        INVOCATION_ID_FIELD: invocation.id,
                    },
                ),
            )
            return PaidInvokeSuccess(
                invocation=invocation,
                response_headers=(
                    self._build_response_headers(
                        SettleOutcome.model_validate(attempt.settle_outcome)
                    )
                    if attempt.settle_outcome
                    else {}
                ),
            )

        if attempt.status is PaymentAttemptStatus.VERIFY_FAILED:
            return self._challenge(payment_requirement, detail="payment could not be verified")

        if attempt.status is PaymentAttemptStatus.SETTLE_FAILED:
            raise UpstreamError("payment settlement failed")

        if attempt.status is PaymentAttemptStatus.CHALLENGED:
            if not payment_payload.matches(payment_requirement):
                await self._mark_verify_failed(
                    attempt,
                    verify_outcome=VerifyOutcome.asset_mismatch(),
                    quote_id=quote.id,
                )
                return self._challenge(payment_requirement, detail="payment could not be verified")

            verify_outcome = await self._verify(
                requirement=payment_requirement,
                payload=payment_payload,
            )
            if not verify_outcome.accepted:
                await self._mark_verify_failed(
                    attempt,
                    verify_outcome=verify_outcome,
                    quote_id=quote.id,
                )
                return self._challenge(payment_requirement, detail="payment could not be verified")

            attempt.verify_outcome = verify_outcome.model_dump(mode="json")
            attempt.status = PaymentAttemptStatus.VERIFIED
            await self._session.commit()

        if attempt.status is PaymentAttemptStatus.VERIFIED:
            settle_outcome = await self._settle(
                requirement=payment_requirement,
                payload=payment_payload,
            )
            attempt.settle_outcome = settle_outcome.model_dump(mode="json")
            attempt.facilitator_reference = settle_outcome.reference
            if not settle_outcome.success:
                await self._mark_settle_failed(
                    attempt,
                    quote_id=quote.id,
                )
                raise UpstreamError("payment settlement failed")

            attempt.status = PaymentAttemptStatus.SETTLED
            await self._session.commit()

        if attempt.status in {
            PaymentAttemptStatus.SETTLED,
            PaymentAttemptStatus.COMPENSATION_REQUIRED,
        }:
            invocation = await invoke.execute(
                session=self._session,
                account_id=actor.account_id,
                resolved=resolved,
                idempotency_key=attempt.idempotency_key,
                http_client=self._http_client,
            )
            if invocation.status is InvocationStatus.FAILED:
                raise invoke.exception_for_failed_invocation(invocation)
            attempt.invocation_id = invocation.id
            await self._ledger_service.record_paid_invocation(
                provider_account_id=resolved.service.provider_account_id,
                service_id=resolved.service.id,
                invocation_id=invocation.id,
                payment_attempt_id=attempt.id,
                amount_minor=quote.amount_minor,
                currency=quote.currency or "",
            )
            logger.info(
                "payment settled",
                extra=build_event_context(
                    "payment.settled",
                    **{
                        PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                        QUOTE_ID_FIELD: quote.id,
                        INVOCATION_ID_FIELD: invocation.id,
                        PROVIDER_ACCOUNT_ID_FIELD: resolved.service.provider_account_id,
                        SERVICE_ID_FIELD: resolved.service.id,
                    },
                ),
            )
            payout_service = PayoutExecutionService(self._session)
            payment_token = self._settings.payment_token
            assert payment_token is not None
            await payout_service.record_ready_payout(
                provider_account_id=resolved.service.provider_account_id,
                service_id=resolved.service.id,
                invocation_id=invocation.id,
                payment_attempt_id=attempt.id,
                gross_amount_minor=payment_requirement.payment_amount,
                currency=payment_token.symbol,
                network=self._settings.x402_network,
            )
            attempt.status = PaymentAttemptStatus.CONSUMED
            await self._session.commit()
            stored_settle_outcome = attempt.settle_outcome
            assert stored_settle_outcome is not None
            return PaidInvokeSuccess(
                invocation=invocation,
                response_headers=self._build_response_headers(
                    SettleOutcome.model_validate(stored_settle_outcome)
                ),
            )

        msg = f"unsupported payment attempt status: {attempt.status.value}"
        raise RuntimeError(msg)

    def _build_requirement(
        self,
        *,
        amount_minor: int,
        currency: str | None,
    ) -> PaymentRequirement:
        try:
            return build_payment_requirement(
                amount_minor=amount_minor,
                currency=currency,
                treasury_address=self._settings.treasury_address,
                payment_token=self._settings.payment_token,
                facilitator_url=self._settings.x402_facilitator_url,
                network=self._settings.x402_network,
                network_caip2=self._settings.x402_network_caip2,
            )
        except PaymentRequirementConfigError as exc:
            raise ConflictError(str(exc)) from exc

    def _challenge(
        self,
        payment_requirement: PaymentRequirement,
        *,
        detail: str,
    ) -> PaymentRequiredChallenge:
        headers = self._x402_resource_server.build_payment_required_headers(
            requirement=payment_requirement,
        )
        return PaymentRequiredChallenge(headers=headers, body={"detail": detail})

    def _build_response_headers(self, settle_outcome: SettleOutcome) -> dict[str, str]:
        return self._x402_resource_server.build_payment_response_headers(
            outcome=settle_outcome,
        )

    async def build_success_headers_for_invocation(self, invocation_id: int) -> dict[str, str]:
        attempt = await self._attempt_repo.get_by_invocation_id(invocation_id=invocation_id)
        if (
            attempt is None
            or attempt.status is not PaymentAttemptStatus.CONSUMED
            or not attempt.settle_outcome
        ):
            return {}
        return self._build_response_headers(SettleOutcome.model_validate(attempt.settle_outcome))

    async def _get_or_create_attempt(
        self,
        actor: ActorContext,
        *,
        quote_id: int,
        idempotency_key: str,
        payment_requirement: PaymentRequirement,
        payment_payload: PaymentPayload,
    ) -> tuple[PaymentAttempt, bool]:
        attempt = await self._attempt_repo.get_by_payment_identifier(
            payment_identifier=payment_payload.identifier,
        )
        if attempt is not None:
            return attempt, False

        attempt = self._attempt_repo.add(
            consumer_account_id=actor.account_id,
            quote_id=quote_id,
            invocation_id=None,
            idempotency_key=idempotency_key,
            payment_identifier=payment_payload.identifier,
            status=PaymentAttemptStatus.CHALLENGED,
            payment_requirement=payment_requirement.model_dump(mode="json"),
            payment_payload=payment_payload.wire,
            verify_outcome=None,
            settle_outcome=None,
            facilitator_reference=None,
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._attempt_repo.get_by_payment_identifier(
                payment_identifier=payment_payload.identifier,
            )
            if existing is not None:
                return existing, True
            raise
        return attempt, False

    async def _mark_verify_failed(
        self,
        attempt: PaymentAttempt,
        *,
        verify_outcome: VerifyOutcome,
        quote_id: int,
    ) -> None:
        attempt.verify_outcome = verify_outcome.model_dump(mode="json")
        attempt.status = PaymentAttemptStatus.VERIFY_FAILED
        logger.info(
            "payment verification failed",
            extra=build_event_context(
                "payment.verify_failed",
                **{
                    PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                    QUOTE_ID_FIELD: quote_id,
                },
            ),
        )
        await self._session.commit()

    async def _mark_settle_failed(
        self,
        attempt: PaymentAttempt,
        *,
        quote_id: int,
    ) -> None:
        attempt.status = PaymentAttemptStatus.SETTLE_FAILED
        logger.error(
            "payment settlement failed",
            extra=build_event_context(
                "payment.settle_failed",
                **{
                    PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                    QUOTE_ID_FIELD: quote_id,
                },
            ),
        )
        await self._session.commit()

    async def _verify(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> VerifyOutcome:
        try:
            return await self._facilitator_client.verify(
                requirement=requirement,
                payload=payload,
            )
        except FacilitatorAuthError as exc:
            raise UpstreamError("facilitator authentication failed") from exc
        except FacilitatorError as exc:
            raise UpstreamError(str(exc)) from exc

    async def _settle(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> SettleOutcome:
        try:
            return await self._facilitator_client.settle(
                requirement=requirement,
                payload=payload,
            )
        except FacilitatorAuthError as exc:
            raise UpstreamError("facilitator authentication failed") from exc
        except FacilitatorError as exc:
            raise UpstreamError(str(exc)) from exc
