from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from sqlalchemy.exc import IntegrityError

from app.core.enums import PaymentAttemptStatus, PricingModelType
from app.core.logging import (
    INVOCATION_ID_FIELD,
    PAYMENT_ATTEMPT_ID_FIELD,
    PROVIDER_ACCOUNT_ID_FIELD,
    QUOTE_ID_FIELD,
    SERVICE_ID_FIELD,
    build_event_context,
    get_logger,
)
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
from app.services.ledger_service import LedgerService
from app.services.payout_service import PayoutExecutionService

logger = get_logger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.core.config import Settings
    from app.db.models import Invocation, PaymentAttempt
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
        self._ledger_service = LedgerService(session)

    async def handle_paid_invoke(
        self,
        actor: ActorContext,
        *,
        resolved: ResolvedInvokeTarget,
        idempotency_key: str,
        request_headers: dict[str, str],
    ) -> PaymentRequiredChallenge | PaidInvokeSuccess:
        quote = resolved.quote
        if quote is None:
            raise InvokeConflictError("paid invoke requires quote")
        quote_id = quote.id
        service_id = resolved.service.id
        endpoint_key = resolved.endpoint.key
        payload = resolved.payload
        if quote.pricing_type is not PricingModelType.FIXED_PER_CALL or quote.amount_minor is None:
            raise InvokeConflictError("payment currency is not supported")

        payment_requirement = self._build_requirement(
            amount_minor=quote.amount_minor,
            currency=quote.currency,
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

        attempt, target_needs_reload = await self._get_or_create_attempt(
            actor,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            payment_identifier=payment_identifier,
            payment_requirement=payment_requirement,
            payment_payload=payment_payload,
        )
        if attempt.quote_id != quote_id:
            raise InvokeConflictError("payment identifier already used")
        if target_needs_reload:
            resolved = await self._invoke_service.resolve_target(
                actor,
                service_id_or_slug=str(service_id),
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
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
        attempt: PaymentAttempt,
    ) -> PaymentRequiredChallenge | PaidInvokeSuccess:
        quote = resolved.quote
        assert quote is not None
        assert quote.amount_minor is not None
        if attempt.status is PaymentAttemptStatus.CONSUMED and attempt.invocation_id is not None:
            invocation = await self._invoke_service.get_invocation(
                actor,
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
                    self._build_response_headers(attempt.settle_outcome)
                    if attempt.settle_outcome
                    else {}
                ),
            )

        if attempt.status is PaymentAttemptStatus.VERIFY_FAILED:
            return self._challenge(payment_requirement, detail="payment could not be verified")

        if attempt.status is PaymentAttemptStatus.SETTLE_FAILED:
            raise InvokeBadGatewayError("payment settlement failed")

        if attempt.status is PaymentAttemptStatus.COMPENSATION_REQUIRED:
            raise InvokeBadGatewayError("settled payment requires manual compensation")

        if attempt.status is PaymentAttemptStatus.CHALLENGED:
            if not _payment_payload_matches_requirement(
                payment_payload=payment_payload,
                payment_requirement=payment_requirement,
            ):
                mismatch_verify_outcome: dict[str, object] = {
                    "ok": False,
                    "reason": "payment asset mismatch",
                }
                await self._mark_verify_failed(
                    attempt,
                    verify_outcome=mismatch_verify_outcome,
                    quote_id=quote.id,
                )
                return self._challenge(payment_requirement, detail="payment could not be verified")

            verify_outcome = await self._verify(
                payment_requirement=payment_requirement,
                payment_payload=payment_payload,
            )
            if not _is_verify_success(verify_outcome):
                await self._mark_verify_failed(
                    attempt,
                    verify_outcome=verify_outcome,
                    quote_id=quote.id,
                )
                return self._challenge(payment_requirement, detail="payment could not be verified")

            attempt.verify_outcome = verify_outcome
            attempt.facilitator_reference = _extract_reference(verify_outcome)
            attempt.status = PaymentAttemptStatus.VERIFIED
            await self._session.commit()

        if attempt.status is PaymentAttemptStatus.VERIFIED:
            settle_outcome = await self._settle(
                payment_requirement=payment_requirement,
                payment_payload=payment_payload,
            )
            attempt.settle_outcome = settle_outcome
            attempt.facilitator_reference = _extract_reference(
                settle_outcome
            ) or _extract_reference(attempt.verify_outcome or {})
            if not _is_settle_success(settle_outcome):
                await self._mark_settle_failed(
                    attempt,
                    quote_id=quote.id,
                )
                raise InvokeBadGatewayError("payment settlement failed")

            attempt.status = PaymentAttemptStatus.SETTLED
            await self._session.commit()

        if attempt.status is PaymentAttemptStatus.SETTLED:
            try:
                invocation = await self._invoke_service.execute(
                    actor,
                    resolved=resolved,
                    idempotency_key=attempt.idempotency_key,
                )
            except (InvokeBadGatewayError, InvokeGatewayTimeoutError):
                failed_invocation = await self._invoke_service.get_invocation_by_idempotency_key(
                    actor,
                    idempotency_key=attempt.idempotency_key,
                )
                attempt.invocation_id = (
                    failed_invocation.id if failed_invocation is not None else None
                )
                attempt.status = PaymentAttemptStatus.COMPENSATION_REQUIRED
                await self._session.commit()
                logger.error(
                    "settled payment requires compensation",
                    extra=build_event_context(
                        "payment.compensation_required",
                        **{
                            PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                            QUOTE_ID_FIELD: quote.id,
                            INVOCATION_ID_FIELD: attempt.invocation_id,
                            PROVIDER_ACCOUNT_ID_FIELD: resolved.service.provider_account_id,
                            SERVICE_ID_FIELD: resolved.service.id,
                        },
                    ),
                )
                raise
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
                gross_amount_minor=_get_payment_amount(payment_requirement),
                currency=payment_token.symbol,
                network=self._settings.x402_network,
            )
            attempt.status = PaymentAttemptStatus.CONSUMED
            await self._session.commit()
            settle_outcome = attempt.settle_outcome
            assert settle_outcome is not None
            return PaidInvokeSuccess(
                invocation=invocation,
                response_headers=self._build_response_headers(settle_outcome),
            )

        msg = f"unsupported payment attempt status: {attempt.status.value}"
        raise RuntimeError(msg)

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
                treasury_address=self._settings.treasury_address,
                payment_token=self._settings.payment_token,
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

    async def build_success_headers_for_invocation(self, invocation_id: int) -> dict[str, str]:
        attempt = await self._attempt_repo.get_by_invocation_id(invocation_id=invocation_id)
        if (
            attempt is None
            or attempt.status is not PaymentAttemptStatus.CONSUMED
            or attempt.settle_outcome is None
        ):
            return {}
        return self._build_response_headers(attempt.settle_outcome)

    async def _get_or_create_attempt(
        self,
        actor: ActorContext,
        *,
        quote_id: int,
        idempotency_key: str,
        payment_identifier: str,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> tuple[PaymentAttempt, bool]:
        attempt = await self._attempt_repo.get_by_payment_identifier(
            payment_identifier=payment_identifier,
        )
        if attempt is not None:
            return attempt, False

        attempt = self._attempt_repo.add(
            consumer_account_id=actor.account_id,
            quote_id=quote_id,
            invocation_id=None,
            idempotency_key=idempotency_key,
            payment_identifier=payment_identifier,
            status=PaymentAttemptStatus.CHALLENGED,
            payment_requirement=payment_requirement,
            payment_payload=payment_payload,
            verify_outcome=None,
            settle_outcome=None,
            facilitator_reference=None,
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._attempt_repo.get_by_payment_identifier(
                payment_identifier=payment_identifier,
            )
            if existing is not None:
                return existing, True
            raise
        return attempt, False

    async def _mark_verify_failed(
        self,
        attempt: PaymentAttempt,
        *,
        verify_outcome: dict[str, object],
        quote_id: int,
    ) -> None:
        attempt.verify_outcome = verify_outcome
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


def _get_payment_amount(payment_requirement: dict[str, object]) -> int:
    value = payment_requirement.get("payment_amount")
    if isinstance(value, int):
        return value
    msg = "payment requirement is missing payment_amount"
    raise InvokeBadGatewayError(msg)


def _payment_payload_matches_requirement(
    *,
    payment_payload: dict[str, object],
    payment_requirement: dict[str, object],
) -> bool:
    accepted = payment_payload.get("accepted")
    if not isinstance(accepted, Mapping):
        return False
    accepted_mapping = cast("Mapping[str, object]", accepted)
    accepted_asset = accepted_mapping.get("asset")
    required_asset = payment_requirement.get("asset")
    if not isinstance(accepted_asset, str) or not isinstance(required_asset, str):
        return False
    return accepted_asset.casefold() == required_asset.casefold()
