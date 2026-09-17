"""The paid invoke: claim the payment, settle it once, invoke once, and record the result.

Every status change is a compare-and-set against the status the caller believes the
attempt is in, so two workers holding the same payment identifier can never both act.
Claims are committed before the facilitator is called and completions after it, which
keeps a settle that is never answered visible as `settlement_unknown` rather than as
something safe to retry. Nothing here rolls the session back: a rollback expires every
loaded object, and the shared session outlives this flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.enums import InvocationStatus, PaymentAttemptStatus
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
from app.db.models import PaymentAttempt, Quote, Service
from app.integrations.x402.facilitator_client import (
    FacilitatorAuthError,
    FacilitatorConfigError,
    FacilitatorError,
)
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
from app.services import invoke, ledger
from app.services.payout_service import PayoutExecutionService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.core.config import Settings
    from app.db.models import Invocation
    from app.integrations.provider_gateway.client import SupportsRequest
    from app.integrations.x402.protocols import (
        SupportsFacilitatorClient,
        SupportsX402ResourceServer,
    )
    from app.services.invoke import ResolvedInvokeTarget

logger = get_logger(__name__)

# Slack on top of the facilitator call timeout so a worker still waiting on a slow
# facilitator is never mistaken for one that died.
SETTLE_LEASE_GRACE_SECONDS = 30

IDENTIFIER_REUSED_DETAIL = "payment identifier already used"
IN_PROGRESS_DETAIL = "payment already in progress"
SETTLEMENT_UNKNOWN_DETAIL = "payment settlement outcome is unknown; recovery required"
COMPENSATION_REQUIRED_DETAIL = "payment requires compensation; the invocation did not succeed"
RECOVERY_REQUIRED_DETAIL = "paid invocation requires recovery"


@dataclass(frozen=True, slots=True)
class PaymentRequiredChallenge:
    headers: dict[str, str]
    body: dict[str, str]


@dataclass(frozen=True, slots=True)
class PaidInvokeSuccess:
    invocation: Invocation
    response_headers: dict[str, str]


async def handle_paid_invoke(
    *,
    session: AsyncSession,
    actor: ActorContext,
    resolved: ResolvedInvokeTarget,
    idempotency_key: str,
    payment_signature: str | None,
    facilitator_client: SupportsFacilitatorClient,
    x402_resource_server: SupportsX402ResourceServer,
    http_client: SupportsRequest,
    settings: Settings,
) -> PaymentRequiredChallenge | PaidInvokeSuccess:
    """Answer one paid invoke, from the payer's header to a settled and recorded result."""
    quote = resolved.quote
    if quote is None:
        raise ConflictError("paid invoke requires quote")
    if quote.amount_minor is None:
        raise ConflictError("quote has no amount")

    requirement = _build_requirement(
        amount_minor=quote.amount_minor,
        currency=quote.currency,
        settings=settings,
    )
    if payment_signature is None:
        return _challenge(
            requirement,
            detail="payment required",
            x402_resource_server=x402_resource_server,
        )
    try:
        payload = PaymentPayload.from_header(payment_signature)
    except InvalidPaymentPayloadError:
        return _challenge(
            requirement,
            detail="payment required",
            x402_resource_server=x402_resource_server,
        )

    attempt = await _claim_attempt(
        session,
        actor=actor,
        quote=quote,
        idempotency_key=idempotency_key,
        payload=payload,
        requirement=requirement,
    )
    return await _resume(
        session,
        actor=actor,
        attempt=attempt,
        resolved=resolved,
        quote=quote,
        requirement=requirement,
        payload=payload,
        facilitator_client=facilitator_client,
        x402_resource_server=x402_resource_server,
        http_client=http_client,
        settings=settings,
    )


async def finish_replayed_invocation(
    *,
    session: AsyncSession,
    actor: ActorContext,
    invocation: Invocation,
    x402_resource_server: SupportsX402ResourceServer,
    settings: Settings,
) -> PaidInvokeSuccess:
    """Answer a repeated paid invoke, finishing the bookkeeping its first run left undone.

    The invocation is already terminal, so this never forwards anything again: it only
    records what the settled payment still owes, which is either the ledger and the
    payout or the compensation a failed provider call requires.
    """
    attempt = await _find_attempt_for_invocation(session, actor=actor, invocation=invocation)

    if attempt.status is PaymentAttemptStatus.CONSUMED:
        return PaidInvokeSuccess(
            invocation=invocation,
            response_headers=_response_headers(
                attempt,
                x402_resource_server=x402_resource_server,
            ),
        )
    if attempt.status is PaymentAttemptStatus.COMPENSATION_REQUIRED:
        if invocation.status is InvocationStatus.FAILED:
            raise invoke.exception_for_failed_invocation(invocation)
        raise ConflictError(COMPENSATION_REQUIRED_DETAIL)
    if attempt.status is not PaymentAttemptStatus.SETTLED:
        raise ConflictError(RECOVERY_REQUIRED_DETAIL)

    if invocation.status is InvocationStatus.FAILED:
        await _require_compensation(session, attempt=attempt, invocation=invocation)
        raise invoke.exception_for_failed_invocation(invocation)

    quote = await session.get(Quote, attempt.quote_id)
    service = await session.get(Service, invocation.service_id)
    if quote is None or service is None:
        raise ConflictError(RECOVERY_REQUIRED_DETAIL)
    return await _finish_settled(
        session,
        attempt=attempt,
        invocation=invocation,
        provider_account_id=service.provider_account_id,
        service_id=service.id,
        quote=quote,
        requirement=PaymentRequirement.model_validate(attempt.payment_requirement),
        x402_resource_server=x402_resource_server,
        settings=settings,
    )


async def _resume(
    session: AsyncSession,
    *,
    actor: ActorContext,
    attempt: PaymentAttempt,
    resolved: ResolvedInvokeTarget,
    quote: Quote,
    requirement: PaymentRequirement,
    payload: PaymentPayload,
    facilitator_client: SupportsFacilitatorClient,
    x402_resource_server: SupportsX402ResourceServer,
    http_client: SupportsRequest,
    settings: Settings,
) -> PaymentRequiredChallenge | PaidInvokeSuccess:
    """Carry the claimed attempt forward from whatever state it is already in."""
    invocation_id = attempt.invocation_id
    if attempt.status is PaymentAttemptStatus.CONSUMED and invocation_id is not None:
        return await _replay_consumed(
            session,
            actor=actor,
            attempt=attempt,
            invocation_id=invocation_id,
            x402_resource_server=x402_resource_server,
        )

    if attempt.status is PaymentAttemptStatus.VERIFY_FAILED:
        return _challenge(
            requirement,
            detail="payment could not be verified",
            x402_resource_server=x402_resource_server,
        )
    if attempt.status is PaymentAttemptStatus.SETTLE_FAILED:
        raise UpstreamError("payment settlement failed")
    if attempt.status is PaymentAttemptStatus.SETTLING:
        raise _settlement_in_flight_error(attempt)
    if attempt.status is PaymentAttemptStatus.SETTLEMENT_UNKNOWN:
        raise ConflictError(SETTLEMENT_UNKNOWN_DETAIL)
    if attempt.status is PaymentAttemptStatus.COMPENSATION_REQUIRED:
        raise ConflictError(COMPENSATION_REQUIRED_DETAIL)

    if attempt.status is PaymentAttemptStatus.CHALLENGED:
        challenge = await _verify(
            session,
            attempt=attempt,
            requirement=requirement,
            payload=payload,
            facilitator_client=facilitator_client,
            x402_resource_server=x402_resource_server,
        )
        if challenge is not None:
            return challenge

    if attempt.status is PaymentAttemptStatus.VERIFIED:
        await _settle(
            session,
            attempt=attempt,
            requirement=requirement,
            payload=payload,
            facilitator_client=facilitator_client,
            settings=settings,
        )

    if attempt.status is PaymentAttemptStatus.SETTLED:
        invocation = await invoke.execute(
            session=session,
            account_id=actor.account_id,
            resolved=resolved,
            idempotency_key=attempt.idempotency_key,
            http_client=http_client,
        )
        if invocation.status is InvocationStatus.FAILED:
            await _require_compensation(session, attempt=attempt, invocation=invocation)
            raise invoke.exception_for_failed_invocation(invocation)
        return await _finish_settled(
            session,
            attempt=attempt,
            invocation=invocation,
            provider_account_id=resolved.service.provider_account_id,
            service_id=resolved.service.id,
            quote=quote,
            requirement=requirement,
            x402_resource_server=x402_resource_server,
            settings=settings,
        )

    msg = f"unsupported payment attempt status: {attempt.status.value}"
    raise RuntimeError(msg)


async def _claim_attempt(
    session: AsyncSession,
    *,
    actor: ActorContext,
    quote: Quote,
    idempotency_key: str,
    payload: PaymentPayload,
    requirement: PaymentRequirement,
) -> PaymentAttempt:
    """Insert the challenged attempt, or load the one that already owns this identifier.

    Conflicts are skipped rather than raised so the shared session never has to roll
    back, which would expire every loaded object.
    """
    claim = (
        insert(PaymentAttempt)
        .values(
            consumer_account_id=actor.account_id,
            quote_id=quote.id,
            invocation_id=None,
            idempotency_key=idempotency_key,
            payment_identifier=payload.identifier,
            status=PaymentAttemptStatus.CHALLENGED,
            payment_requirement=requirement.model_dump(mode="json"),
            payment_payload=payload.wire,
            verify_outcome=None,
            settle_outcome=None,
            facilitator_reference=None,
            settle_in_progress_until=None,
        )
        .on_conflict_do_nothing(index_elements=["payment_identifier"])
        .returning(PaymentAttempt)
    )
    attempt = await session.scalar(claim)
    await session.commit()
    if attempt is None:
        attempt = await session.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.payment_identifier == payload.identifier,
            ),
        )
    if attempt is None:
        raise ConflictError(IDENTIFIER_REUSED_DETAIL)

    # One payment identifier pays for one caller's one request. The request body is
    # bound through the quote and through the invocation claim, so these three are
    # everything the attempt has to agree with.
    if attempt.consumer_account_id != actor.account_id:
        raise ConflictError(IDENTIFIER_REUSED_DETAIL)
    if attempt.quote_id != quote.id:
        raise ConflictError(IDENTIFIER_REUSED_DETAIL)
    if attempt.idempotency_key != idempotency_key:
        raise ConflictError(IDENTIFIER_REUSED_DETAIL)
    return attempt


async def _verify(
    session: AsyncSession,
    *,
    attempt: PaymentAttempt,
    requirement: PaymentRequirement,
    payload: PaymentPayload,
    facilitator_client: SupportsFacilitatorClient,
    x402_resource_server: SupportsX402ResourceServer,
) -> PaymentRequiredChallenge | None:
    """Decide whether the payment may proceed, leaving the attempt verified or refused.

    Verification moves no funds, so a facilitator fault here leaves the attempt exactly
    as it was and the payer owes nothing.
    """
    if not payload.matches(requirement):
        return await _record_verify_failure(
            session,
            attempt=attempt,
            outcome=VerifyOutcome.asset_mismatch(),
            requirement=requirement,
            x402_resource_server=x402_resource_server,
        )

    try:
        outcome = await facilitator_client.verify(requirement=requirement, payload=payload)
    except FacilitatorAuthError as exc:
        raise UpstreamError("facilitator authentication failed") from exc
    except FacilitatorError as exc:
        raise UpstreamError(str(exc)) from exc

    if not outcome.accepted:
        return await _record_verify_failure(
            session,
            attempt=attempt,
            outcome=outcome,
            requirement=requirement,
            x402_resource_server=x402_resource_server,
        )

    verified = await _transition(
        session,
        attempt,
        expected=PaymentAttemptStatus.CHALLENGED,
        to=PaymentAttemptStatus.VERIFIED,
        verify_outcome=outcome.model_dump(mode="json"),
    )
    if not verified:
        raise ConflictError(IN_PROGRESS_DETAIL)
    await session.commit()
    return None


async def _record_verify_failure(
    session: AsyncSession,
    *,
    attempt: PaymentAttempt,
    outcome: VerifyOutcome,
    requirement: PaymentRequirement,
    x402_resource_server: SupportsX402ResourceServer,
) -> PaymentRequiredChallenge:
    refused = await _transition(
        session,
        attempt,
        expected=PaymentAttemptStatus.CHALLENGED,
        to=PaymentAttemptStatus.VERIFY_FAILED,
        verify_outcome=outcome.model_dump(mode="json"),
    )
    if not refused:
        raise ConflictError(IN_PROGRESS_DETAIL)
    await session.commit()
    logger.info(
        "payment verification failed",
        extra=build_event_context(
            "payment.verify_failed",
            **{
                PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                QUOTE_ID_FIELD: attempt.quote_id,
            },
        ),
    )
    return _challenge(
        requirement,
        detail="payment could not be verified",
        x402_resource_server=x402_resource_server,
    )


async def _settle(
    session: AsyncSession,
    *,
    attempt: PaymentAttempt,
    requirement: PaymentRequirement,
    payload: PaymentPayload,
    facilitator_client: SupportsFacilitatorClient,
    settings: Settings,
) -> None:
    """Move the payer's funds exactly once, recording the claim before the call."""
    lease_until = datetime.now(UTC) + timedelta(
        seconds=settings.x402_facilitator_timeout_seconds + SETTLE_LEASE_GRACE_SECONDS,
    )
    claimed = await _transition(
        session,
        attempt,
        expected=PaymentAttemptStatus.VERIFIED,
        to=PaymentAttemptStatus.SETTLING,
        settle_in_progress_until=lease_until,
    )
    if not claimed:
        raise ConflictError(IN_PROGRESS_DETAIL)
    # Ownership of the settle becomes durable here, before the funds can move, so the
    # call below can never be the only record that it was attempted.
    await session.commit()

    try:
        outcome = await facilitator_client.settle(requirement=requirement, payload=payload)
    except (FacilitatorAuthError, FacilitatorConfigError) as exc:
        # These are refused before the facilitator executes anything, so the attempt is
        # as payable as it was and goes back to where a retry can settle it.
        _require_settling_owner(
            await _transition(
                session,
                attempt,
                expected=PaymentAttemptStatus.SETTLING,
                to=PaymentAttemptStatus.VERIFIED,
                settle_in_progress_until=None,
            ),
        )
        await session.commit()
        detail = (
            "facilitator authentication failed"
            if isinstance(exc, FacilitatorAuthError)
            else str(exc)
        )
        raise UpstreamError(detail) from exc
    except FacilitatorError as exc:
        # No usable answer came back, so the funds may or may not have moved. The row
        # says so, and nothing settles this identifier again without a human.
        _require_settling_owner(
            await _transition(
                session,
                attempt,
                expected=PaymentAttemptStatus.SETTLING,
                to=PaymentAttemptStatus.SETTLEMENT_UNKNOWN,
                settle_in_progress_until=None,
            ),
        )
        await session.commit()
        logger.error(
            "payment settlement outcome is unknown",
            extra=build_event_context(
                "payment.settlement_unknown",
                **{
                    PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                    QUOTE_ID_FIELD: attempt.quote_id,
                },
            ),
        )
        raise UpstreamError(str(exc)) from exc

    if not outcome.success:
        _require_settling_owner(
            await _transition(
                session,
                attempt,
                expected=PaymentAttemptStatus.SETTLING,
                to=PaymentAttemptStatus.SETTLE_FAILED,
                settle_outcome=outcome.model_dump(mode="json"),
                facilitator_reference=outcome.reference,
                settle_in_progress_until=None,
            ),
        )
        await session.commit()
        logger.error(
            "payment settlement failed",
            extra=build_event_context(
                "payment.settle_failed",
                **{
                    PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                    QUOTE_ID_FIELD: attempt.quote_id,
                },
            ),
        )
        raise UpstreamError("payment settlement failed")

    _require_settling_owner(
        await _transition(
            session,
            attempt,
            expected=PaymentAttemptStatus.SETTLING,
            to=PaymentAttemptStatus.SETTLED,
            settle_outcome=outcome.model_dump(mode="json"),
            facilitator_reference=outcome.reference,
            settle_in_progress_until=None,
        ),
    )
    await session.commit()


async def _finish_settled(
    session: AsyncSession,
    *,
    attempt: PaymentAttempt,
    invocation: Invocation,
    provider_account_id: int,
    service_id: int,
    quote: Quote,
    requirement: PaymentRequirement,
    x402_resource_server: SupportsX402ResourceServer,
    settings: Settings,
) -> PaidInvokeSuccess:
    """Record the money the succeeded invocation earned and consume the attempt, at once."""
    amount_minor = quote.amount_minor
    if amount_minor is None:
        raise ConflictError("quote has no amount")
    payment_token = settings.payment_token
    if payment_token is None:
        raise ConflictError("payment network is not supported")

    await ledger.record_paid_invocation(
        session=session,
        provider_account_id=provider_account_id,
        service_id=service_id,
        invocation_id=invocation.id,
        payment_attempt_id=attempt.id,
        amount_minor=amount_minor,
        currency=quote.currency or "",
    )
    await PayoutExecutionService(session).record_ready_payout(
        provider_account_id=provider_account_id,
        service_id=service_id,
        invocation_id=invocation.id,
        payment_attempt_id=attempt.id,
        gross_amount_minor=requirement.payment_amount,
        currency=payment_token.symbol,
        network=settings.x402_network,
    )
    consumed = await _transition(
        session,
        attempt,
        expected=PaymentAttemptStatus.SETTLED,
        to=PaymentAttemptStatus.CONSUMED,
        invocation_id=invocation.id,
    )
    if not consumed:
        # Another worker finished the same attempt first. Its ledger entries and its
        # payout are the same rows this one would have written, so committing nothing
        # here loses nothing.
        await _reload(session, attempt)
        if attempt.status is not PaymentAttemptStatus.CONSUMED:
            raise ConflictError(IN_PROGRESS_DETAIL)
        return PaidInvokeSuccess(
            invocation=invocation,
            response_headers=_response_headers(
                attempt,
                x402_resource_server=x402_resource_server,
            ),
        )
    await session.commit()

    logger.info(
        "ledger entries recorded",
        extra=build_event_context(
            "ledger.recorded",
            **{
                PROVIDER_ACCOUNT_ID_FIELD: provider_account_id,
                SERVICE_ID_FIELD: service_id,
                INVOCATION_ID_FIELD: invocation.id,
                PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
            },
        ),
    )
    logger.info(
        "payment settled",
        extra=build_event_context(
            "payment.settled",
            **{
                PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                QUOTE_ID_FIELD: quote.id,
                INVOCATION_ID_FIELD: invocation.id,
                PROVIDER_ACCOUNT_ID_FIELD: provider_account_id,
                SERVICE_ID_FIELD: service_id,
            },
        ),
    )
    return PaidInvokeSuccess(
        invocation=invocation,
        response_headers=_response_headers(
            attempt,
            x402_resource_server=x402_resource_server,
        ),
    )


async def _require_compensation(
    session: AsyncSession,
    *,
    attempt: PaymentAttempt,
    invocation: Invocation,
) -> None:
    """Mark a settled payment whose invocation failed as owing the payer a refund.

    No ledger entry and no payout are written: nothing was earned.
    """
    marked = await _transition(
        session,
        attempt,
        expected=PaymentAttemptStatus.SETTLED,
        to=PaymentAttemptStatus.COMPENSATION_REQUIRED,
        invocation_id=invocation.id,
    )
    if not marked:
        await _reload(session, attempt)
        if attempt.status is not PaymentAttemptStatus.COMPENSATION_REQUIRED:
            raise ConflictError(IN_PROGRESS_DETAIL)
        return
    await session.commit()
    logger.error(
        "payment compensation required",
        extra=build_event_context(
            "payment.compensation_required",
            **{
                PAYMENT_ATTEMPT_ID_FIELD: attempt.id,
                QUOTE_ID_FIELD: attempt.quote_id,
                INVOCATION_ID_FIELD: invocation.id,
            },
        ),
    )


async def _replay_consumed(
    session: AsyncSession,
    *,
    actor: ActorContext,
    attempt: PaymentAttempt,
    invocation_id: int,
    x402_resource_server: SupportsX402ResourceServer,
) -> PaidInvokeSuccess:
    invocation = await invoke.get_invocation(
        session=session,
        account_id=actor.account_id,
        invocation_id=invocation_id,
    )
    logger.info(
        "paid invoke replayed",
        extra=build_event_context(
            "payment.replayed",
            **{INVOCATION_ID_FIELD: invocation.id},
        ),
    )
    return PaidInvokeSuccess(
        invocation=invocation,
        response_headers=_response_headers(
            attempt,
            x402_resource_server=x402_resource_server,
        ),
    )


async def _find_attempt_for_invocation(
    session: AsyncSession,
    *,
    actor: ActorContext,
    invocation: Invocation,
) -> PaymentAttempt:
    attempt = await session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.invocation_id == invocation.id),
    )
    if attempt is None:
        # A worker that died between settling and linking left the invocation unclaimed,
        # so the attempt is found the way the invocation itself was: by its caller and key.
        attempt = await session.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.consumer_account_id == actor.account_id,
                PaymentAttempt.idempotency_key == invocation.idempotency_key,
                PaymentAttempt.status.in_(
                    (
                        PaymentAttemptStatus.SETTLED,
                        PaymentAttemptStatus.COMPENSATION_REQUIRED,
                        PaymentAttemptStatus.CONSUMED,
                    ),
                ),
            ),
        )
    if (
        attempt is None
        or attempt.consumer_account_id != actor.account_id
        or attempt.quote_id != invocation.quote_id
    ):
        raise ConflictError(RECOVERY_REQUIRED_DETAIL)
    return attempt


async def _transition(
    session: AsyncSession,
    attempt: PaymentAttempt,
    *,
    expected: PaymentAttemptStatus,
    to: PaymentAttemptStatus,
    **fields: object,
) -> bool:
    """Move the attempt from one status to the next, or report that someone else did.

    The status the caller believes the row is in is part of the WHERE clause, so the
    database, not a prior read, decides which worker owns the next step.
    """
    changed = await session.scalar(
        update(PaymentAttempt)
        .where(PaymentAttempt.id == attempt.id, PaymentAttempt.status == expected)
        .values(status=to, **fields)
        .returning(PaymentAttempt.id)
        .execution_options(synchronize_session=False),
    )
    if changed is None:
        return False
    await _reload(session, attempt)
    return True


async def _reload(session: AsyncSession, attempt: PaymentAttempt) -> None:
    await session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == attempt.id)
        .execution_options(populate_existing=True),
    )


def _settlement_in_flight_error(attempt: PaymentAttempt) -> ConflictError:
    lease = attempt.settle_in_progress_until
    if lease is not None and lease > datetime.now(UTC):
        return ConflictError("payment settlement in progress")
    # A missing or expired lease says only that the settling worker stopped reporting.
    # The funds may or may not have moved, so nothing is settled again.
    return ConflictError(SETTLEMENT_UNKNOWN_DETAIL)


def _require_settling_owner(transitioned: bool) -> None:
    if not transitioned:
        msg = "the settling payment attempt was changed by another writer"
        raise RuntimeError(msg)


def _build_requirement(
    *,
    amount_minor: int,
    currency: str | None,
    settings: Settings,
) -> PaymentRequirement:
    try:
        return build_payment_requirement(
            amount_minor=amount_minor,
            currency=currency,
            treasury_address=settings.treasury_address,
            payment_token=settings.payment_token,
            facilitator_url=settings.x402_facilitator_url,
            network=settings.x402_network,
            network_caip2=settings.x402_network_caip2,
        )
    except PaymentRequirementConfigError as exc:
        raise ConflictError(str(exc)) from exc


def _challenge(
    requirement: PaymentRequirement,
    *,
    detail: str,
    x402_resource_server: SupportsX402ResourceServer,
) -> PaymentRequiredChallenge:
    return PaymentRequiredChallenge(
        headers=x402_resource_server.build_payment_required_headers(requirement=requirement),
        body={"detail": detail},
    )


def _response_headers(
    attempt: PaymentAttempt,
    *,
    x402_resource_server: SupportsX402ResourceServer,
) -> dict[str, str]:
    if not attempt.settle_outcome:
        return {}
    return x402_resource_server.build_payment_response_headers(
        outcome=SettleOutcome.model_validate(attempt.settle_outcome),
    )
