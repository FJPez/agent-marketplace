from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.enums import PayoutStatus, PricingModelType
from app.core.logging import (
    INVOCATION_ID_FIELD,
    PAYMENT_ATTEMPT_ID_FIELD,
    PAYOUT_ID_FIELD,
    PAYOUT_STATUS_FIELD,
    PROVIDER_ACCOUNT_ID_FIELD,
    QUOTE_ID_FIELD,
    SERVICE_ID_FIELD,
    TRANSFER_REFERENCE_FIELD,
    build_event_context,
    get_logger,
)
from app.db.models import Account
from app.integrations.payouts import PayoutExecutionError, SupportsPayoutExecutor
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
from app.repositories.payout_repo import PayoutRepository
from app.services.invoke_service import (
    InvokeBadGatewayError,
    InvokeConflictError,
    InvokeGatewayTimeoutError,
    InvokeService,
)
from app.services.ledger_service import LedgerService, split_paid_invocation_amount

logger = get_logger(__name__)

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
        payout_executor: SupportsPayoutExecutor | None = None,
    ) -> None:
        self._session = session
        self._facilitator_client = facilitator_client
        self._x402_resource_server = x402_resource_server
        self._settings = settings
        self._attempt_repo = PaymentAttemptRepository(session)
        self._payout_repo = PayoutRepository(session)
        self._invoke_service = InvokeService(session, http_client=http_client)
        self._ledger_service = LedgerService(session)
        self._payout_executor = payout_executor

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
            logger.info(
                "paid invoke replayed",
                extra=build_event_context(
                    "payment.replayed",
                    **{
                        INVOCATION_ID_FIELD: existing.id,
                    },
                ),
            )
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
            logger.info(
                "payment verification failed",
                extra=build_event_context(
                    "payment.verify_failed",
                    **{
                        PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                        QUOTE_ID_FIELD: resolved.quote.id,
                    },
                ),
            )
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
            logger.error(
                "payment settlement failed",
                extra=build_event_context(
                    "payment.settle_failed",
                    **{
                        PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                        QUOTE_ID_FIELD: resolved.quote.id,
                    },
                ),
            )
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
        await self._ledger_service.record_paid_invocation(
            provider_account_id=resolved.service.provider_account_id,
            service_id=resolved.service.id,
            invocation_id=invocation.id,
            payment_attempt_id=attempt.id,
            amount_minor=resolved.quote.amount_minor,
            currency=resolved.quote.currency or "",
        )
        logger.info(
            "payment settled",
            extra=build_event_context(
                "payment.settled",
                **{
                    PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                    QUOTE_ID_FIELD: resolved.quote.id,
                    INVOCATION_ID_FIELD: invocation.id,
                    PROVIDER_ACCOUNT_ID_FIELD: resolved.service.provider_account_id,
                    SERVICE_ID_FIELD: resolved.service.id,
                },
            ),
        )
        await self._record_provider_payout(
            provider_account_id=resolved.service.provider_account_id,
            service_id=resolved.service.id,
            invocation_id=invocation.id,
            payment_attempt_id=attempt.id,
            gross_amount_minor=resolved.quote.amount_minor,
            currency=resolved.quote.currency or "",
        )
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

    async def _record_provider_payout(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        gross_amount_minor: int,
        currency: str,
    ) -> None:
        payout = await self._payout_repo.get_by_payment_attempt_id(
            payment_attempt_id=payment_attempt_id,
        )
        if payout is not None:
            return
        _, provider_amount_minor = split_paid_invocation_amount(gross_amount_minor)
        provider_wallet = await self._get_provider_wallet(provider_account_id=provider_account_id)
        if provider_wallet is None:
            self._payout_repo.add(
                provider_account_id=provider_account_id,
                service_id=service_id,
                invocation_id=invocation_id,
                payment_attempt_id=payment_attempt_id,
                destination_wallet="",
                amount_minor=provider_amount_minor,
                currency=currency,
                network=self._settings.x402_network,
                status=PayoutStatus.FAILED,
                error_message="provider wallet address is not configured",
            )
            return
        payout = self._payout_repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet=provider_wallet,
            amount_minor=provider_amount_minor,
            currency=currency,
            network=self._settings.x402_network,
            status=PayoutStatus.READY,
            attempt_count=0,
        )
        await self._session.flush()
        logger.info(
            "provider payout created",
            extra=build_event_context(
                "payout.ready",
                **{
                    PAYMENT_ATTEMPT_ID_FIELD: payment_attempt_id,
                    PAYOUT_ID_FIELD: payout.id,
                    PAYOUT_STATUS_FIELD: payout.status.value,
                    PROVIDER_ACCOUNT_ID_FIELD: provider_account_id,
                    INVOCATION_ID_FIELD: invocation_id,
                    SERVICE_ID_FIELD: service_id,
                },
            ),
        )
        if provider_amount_minor <= 0:
            payout.status = PayoutStatus.FAILED
            payout.error_message = "provider payout amount must be positive"
            return
        if not self._settings.payouts_enabled or self._payout_executor is None:
            return
        payout.status = PayoutStatus.PENDING
        payout.attempt_count += 1
        try:
            outcome = await self._payout_executor.send_payout(
                destination_wallet=provider_wallet,
                amount_minor=provider_amount_minor,
                idempotency_key=f"payment-attempt:{payment_attempt_id}",
            )
        except (PayoutExecutionError, ValueError, RuntimeError) as exc:
            payout.status = PayoutStatus.FAILED
            payout.error_message = str(exc)
            logger.error(
                "provider payout failed",
                extra=build_event_context(
                    "payout.failed",
                    **{
                        PAYMENT_ATTEMPT_ID_FIELD: payment_attempt_id,
                        PAYOUT_ID_FIELD: payout.id,
                        PAYOUT_STATUS_FIELD: payout.status.value,
                        PROVIDER_ACCOUNT_ID_FIELD: provider_account_id,
                        INVOCATION_ID_FIELD: invocation_id,
                        SERVICE_ID_FIELD: service_id,
                    },
                ),
            )
            return
        payout.status = PayoutStatus.SENT
        payout.transfer_reference = _extract_reference(outcome)
        payout.error_message = None
        logger.info(
            "provider payout sent",
            extra=build_event_context(
                "payout.sent",
                **{
                    PAYMENT_ATTEMPT_ID_FIELD: payment_attempt_id,
                    PAYOUT_ID_FIELD: payout.id,
                    PAYOUT_STATUS_FIELD: payout.status.value,
                    PROVIDER_ACCOUNT_ID_FIELD: provider_account_id,
                    INVOCATION_ID_FIELD: invocation_id,
                    SERVICE_ID_FIELD: service_id,
                    TRANSFER_REFERENCE_FIELD: payout.transfer_reference,
                },
            ),
        )

    async def _get_provider_wallet(self, *, provider_account_id: int) -> str | None:
        statement = select(Account.wallet_address).where(Account.id == provider_account_id)
        wallet_address = await self._session.scalar(statement)
        if wallet_address is None:
            return None
        normalized_wallet = wallet_address.strip()
        return normalized_wallet or None


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
