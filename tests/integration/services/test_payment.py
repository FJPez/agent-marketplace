"""The paid invoke against PostgreSQL: one settlement, one forward, one set of money rows."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    ConsumerAccountFactory,
    InvocationFactory,
    LedgerEntryFactory,
    PaymentAttemptFactory,
    QuoteFactory,
)
from tests.fixtures.payment import (
    PAID_ENDPOINT_KEY,
    PAID_INVOKE_PAYLOAD,
    FacilitatorHook,
    MoneyRowsLoader,
    NeverCalledFacilitatorClient,
    PaidInvokeRunner,
    PaidTarget,
    PaymentAttemptLoader,
    PaymentAttemptsLoader,
    ReplayedPaidInvokeRunner,
    ScriptedFacilitatorFactory,
    ScriptedHttpClient,
)
from tests.helpers.x402 import build_settle_outcome

from app.core.enums import (
    AccessMode,
    InvocationFailureReason,
    InvocationStatus,
    LedgerEntryType,
    PaymentAttemptStatus,
    PayoutStatus,
    PricingModelType,
)
from app.core.errors import ConflictError, UpstreamError
from app.integrations.x402.facilitator_client import FacilitatorAuthError, FacilitatorTimeoutError
from app.integrations.x402.models import SettleOutcome, VerifyOutcome
from app.services.payment import PaidInvokeSuccess, PaymentRequiredChallenge

pytestmark = [pytest.mark.asyncio]

VERIFY_ACCEPTED = VerifyOutcome(accepted=True, payer="0xpayer", checked_by="facilitator")
SETTLE_REJECTED = SettleOutcome(success=False, error_reason="insufficient_funds")
OTHER_ASSET = "0x00000000000000000000000000000000000000aa"
EXPIRED_LEASE = datetime(2020, 1, 1, tzinfo=UTC)
RACE_TIMEOUT_SECONDS = 30


async def test_a_fresh_paid_invoke_settles_forwards_once_and_records_the_money(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    load_payment_attempt: PaymentAttemptLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[build_settle_outcome()],
    )
    http_client = ScriptedHttpClient(
        responses=[Response(status_code=200, json={"result": "bonjour"})],
    )

    outcome = await run_paid_invoke(
        target=paid_target,
        facilitator_client=facilitator_client,
        http_client=http_client,
    )

    attempt = await load_payment_attempt()
    money = await load_money_rows()
    assert isinstance(outcome, PaidInvokeSuccess)
    assert outcome.invocation.status is InvocationStatus.SUCCEEDED
    assert "PAYMENT-RESPONSE" in outcome.response_headers
    assert attempt.status is PaymentAttemptStatus.CONSUMED
    assert attempt.invocation_id == outcome.invocation.id
    assert attempt.settle_in_progress_until is None
    assert [entry.entry_type for entry in money.ledger_entries] == [
        LedgerEntryType.CHARGE,
        LedgerEntryType.PLATFORM_FEE,
        LedgerEntryType.PROVIDER_EARNING,
    ]
    assert len(money.payouts) == 1
    assert money.payouts[0].status is PayoutStatus.READY
    assert len(facilitator_client.verify_calls) == 1
    assert len(facilitator_client.settle_calls) == 1
    assert len(http_client.calls) == 1


async def test_an_asset_mismatch_fails_verification_without_asking_the_facilitator(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    load_payment_attempt: PaymentAttemptLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    outcome = await run_paid_invoke(
        target=paid_target,
        facilitator_client=never_called_facilitator_client,
        asset=OTHER_ASSET,
    )

    attempt = await load_payment_attempt()
    money = await load_money_rows()
    assert isinstance(outcome, PaymentRequiredChallenge)
    assert outcome.body == {"detail": "payment could not be verified"}
    assert attempt.status is PaymentAttemptStatus.VERIFY_FAILED
    assert attempt.verify_outcome is not None
    assert attempt.verify_outcome["checked_by"] == "resource_server"
    assert money.ledger_entries == []
    assert money.payouts == []


async def test_a_verify_fault_leaves_the_attempt_challenged(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    load_payment_attempt: PaymentAttemptLoader,
) -> None:
    facilitator_client = scripted_facilitator_client(
        verify_results=[FacilitatorTimeoutError("facilitator verify failed: timed out")],
    )

    with pytest.raises(UpstreamError):
        await run_paid_invoke(target=paid_target, facilitator_client=facilitator_client)

    attempt = await load_payment_attempt()
    assert attempt.status is PaymentAttemptStatus.CHALLENGED
    assert attempt.verify_outcome is None
    assert facilitator_client.settle_calls == []


async def test_the_settle_claim_is_durable_before_the_facilitator_is_called(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    load_payment_attempt: PaymentAttemptLoader,
) -> None:
    observed: list[tuple[PaymentAttemptStatus, bool]] = []

    async def read_the_claim(session_factory: async_sessionmaker[AsyncSession]) -> None:
        in_flight = await load_payment_attempt()
        observed.append((in_flight.status, in_flight.settle_in_progress_until is not None))

    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[build_settle_outcome()],
        on_settle=read_the_claim,
    )

    await run_paid_invoke(
        target=paid_target,
        facilitator_client=facilitator_client,
        http_client=ScriptedHttpClient(responses=[Response(status_code=200, json={"ok": True})]),
    )

    attempt = await load_payment_attempt()
    assert observed == [(PaymentAttemptStatus.SETTLING, True)]
    assert attempt.status is PaymentAttemptStatus.CONSUMED


async def test_no_transaction_is_open_while_the_facilitator_verifies(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    assert_no_open_transaction: FacilitatorHook,
) -> None:
    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[build_settle_outcome()],
        on_verify=assert_no_open_transaction,
        on_settle=assert_no_open_transaction,
    )

    outcome = await run_paid_invoke(
        target=paid_target,
        facilitator_client=facilitator_client,
        http_client=ScriptedHttpClient(responses=[Response(status_code=200, json={"ok": True})]),
    )

    assert isinstance(outcome, PaidInvokeSuccess)
    assert len(facilitator_client.verify_calls) == 1
    assert len(facilitator_client.settle_calls) == 1


async def test_no_transaction_is_open_while_an_existing_attempt_is_verified(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    payment_attempt_factory: PaymentAttemptFactory,
    assert_no_open_transaction: FacilitatorHook,
) -> None:
    # The claim insert conflicts with this row, so the flow reaches the facilitator by
    # loading the attempt instead of by inserting it.
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        status=PaymentAttemptStatus.CHALLENGED,
    )
    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[build_settle_outcome()],
        on_verify=assert_no_open_transaction,
        on_settle=assert_no_open_transaction,
    )

    outcome = await run_paid_invoke(
        target=paid_target,
        facilitator_client=facilitator_client,
        http_client=ScriptedHttpClient(responses=[Response(status_code=200, json={"ok": True})]),
    )

    assert isinstance(outcome, PaidInvokeSuccess)
    assert len(facilitator_client.verify_calls) == 1
    assert len(facilitator_client.settle_calls) == 1


async def test_an_unanswered_settle_ends_unknown_and_is_never_settled_again(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    load_payment_attempt: PaymentAttemptLoader,
) -> None:
    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[FacilitatorTimeoutError("facilitator settle failed: timed out")],
    )

    with pytest.raises(UpstreamError):
        await run_paid_invoke(target=paid_target, facilitator_client=facilitator_client)
    settled_unknown = await load_payment_attempt()

    with pytest.raises(ConflictError, match="unknown"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
        )

    assert settled_unknown.status is PaymentAttemptStatus.SETTLEMENT_UNKNOWN
    assert settled_unknown.settle_in_progress_until is None
    assert len(facilitator_client.settle_calls) == 1


async def test_a_proven_settle_fault_returns_the_attempt_to_verified_for_one_retry(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    load_payment_attempt: PaymentAttemptLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    refusing_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[FacilitatorAuthError("facilitator authentication failed")],
    )

    with pytest.raises(UpstreamError, match="authentication"):
        await run_paid_invoke(target=paid_target, facilitator_client=refusing_client)
    refused = await load_payment_attempt()

    retry_client = scripted_facilitator_client(settle_results=[build_settle_outcome()])
    outcome = await run_paid_invoke(
        target=paid_target,
        facilitator_client=retry_client,
        http_client=ScriptedHttpClient(responses=[Response(status_code=200, json={"ok": True})]),
    )

    money = await load_money_rows()
    assert refused.status is PaymentAttemptStatus.VERIFIED
    assert refused.settle_in_progress_until is None
    assert isinstance(outcome, PaidInvokeSuccess)
    assert retry_client.verify_calls == []
    assert len(retry_client.settle_calls) == 1
    assert len(money.ledger_entries) == 3


async def test_a_settling_attempt_with_an_expired_lease_requires_recovery(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    payment_attempt_factory: PaymentAttemptFactory,
    load_payment_attempt: PaymentAttemptLoader,
) -> None:
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        status=PaymentAttemptStatus.SETTLING,
        settle_in_progress_until=EXPIRED_LEASE,
    )

    with pytest.raises(ConflictError, match="unknown"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
        )

    attempt = await load_payment_attempt()
    assert attempt.status is PaymentAttemptStatus.SETTLING


async def test_a_settling_attempt_with_a_live_lease_reports_settlement_in_progress(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    payment_attempt_factory: PaymentAttemptFactory,
) -> None:
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        status=PaymentAttemptStatus.SETTLING,
        settle_in_progress_until=datetime.now(UTC) + timedelta(minutes=5),
    )

    with pytest.raises(ConflictError, match="settlement in progress"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
        )


async def test_a_rejected_settlement_ends_in_settle_failed(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    load_payment_attempt: PaymentAttemptLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[SETTLE_REJECTED],
    )

    with pytest.raises(UpstreamError, match="payment settlement failed"):
        await run_paid_invoke(target=paid_target, facilitator_client=facilitator_client)

    attempt = await load_payment_attempt()
    money = await load_money_rows()
    assert attempt.status is PaymentAttemptStatus.SETTLE_FAILED
    assert attempt.settle_in_progress_until is None
    assert money.ledger_entries == []
    assert money.payouts == []


async def test_a_failed_provider_call_requires_compensation_and_records_no_money(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    load_payment_attempt: PaymentAttemptLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[build_settle_outcome()],
    )
    http_client = ScriptedHttpClient(
        responses=[Response(status_code=503, json={"detail": "unavailable"})],
    )

    with pytest.raises(UpstreamError):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=facilitator_client,
            http_client=http_client,
        )
    compensating = await load_payment_attempt()
    money = await load_money_rows()

    with pytest.raises(ConflictError, match="compensation"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
        )

    assert compensating.status is PaymentAttemptStatus.COMPENSATION_REQUIRED
    assert compensating.invocation_id is not None
    assert money.ledger_entries == []
    assert money.payouts == []
    assert len(http_client.calls) == 1


async def test_a_crash_before_the_failure_was_recorded_repairs_into_compensation_required(
    paid_target: PaidTarget,
    run_replayed_paid_invoke: ReplayedPaidInvokeRunner,
    payment_attempt_factory: PaymentAttemptFactory,
    invocation_factory: InvocationFactory,
    load_payment_attempt: PaymentAttemptLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    invocation_id = await invocation_factory(
        consumer_account_id=paid_target.consumer_account_id,
        service_id=paid_target.service_id,
        endpoint_id=paid_target.endpoint_id,
        endpoint_key=PAID_ENDPOINT_KEY,
        access_mode=AccessMode.PAID,
        quote_id=paid_target.quote_id,
        payload=PAID_INVOKE_PAYLOAD,
        status=InvocationStatus.FAILED,
        upstream_status_code=503,
        error_message="upstream request failed",
        failure_reason=InvocationFailureReason.UPSTREAM_RESPONSE,
    )
    # The worker settled the payment and then died before it could record what the
    # failed provider call left owing.
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        invocation_id=None,
        status=PaymentAttemptStatus.SETTLED,
        settle_outcome=build_settle_outcome().model_dump(mode="json"),
    )

    with pytest.raises(UpstreamError):
        await run_replayed_paid_invoke(
            invocation_id=invocation_id,
            account_id=paid_target.consumer_account_id,
        )

    attempt = await load_payment_attempt()
    money = await load_money_rows()
    assert attempt.status is PaymentAttemptStatus.COMPENSATION_REQUIRED
    assert attempt.invocation_id == invocation_id
    assert money.ledger_entries == []
    assert money.payouts == []


async def test_a_partial_commit_repairs_into_consumed_without_duplicating_the_money(
    paid_target: PaidTarget,
    run_replayed_paid_invoke: ReplayedPaidInvokeRunner,
    payment_attempt_factory: PaymentAttemptFactory,
    invocation_factory: InvocationFactory,
    ledger_entry_factory: LedgerEntryFactory,
    load_payment_attempt: PaymentAttemptLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    invocation_id = await invocation_factory(
        consumer_account_id=paid_target.consumer_account_id,
        service_id=paid_target.service_id,
        endpoint_id=paid_target.endpoint_id,
        endpoint_key=PAID_ENDPOINT_KEY,
        access_mode=AccessMode.PAID,
        quote_id=paid_target.quote_id,
        payload=PAID_INVOKE_PAYLOAD,
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
    )
    attempt_id = await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        invocation_id=None,
        status=PaymentAttemptStatus.SETTLED,
        settle_outcome=build_settle_outcome().model_dump(mode="json"),
    )
    # The ledger was written, but the payout and the CONSUMED transition were not.
    for entry_type, amount_minor in (
        (LedgerEntryType.CHARGE, 500),
        (LedgerEntryType.PLATFORM_FEE, 50),
        (LedgerEntryType.PROVIDER_EARNING, 450),
    ):
        await ledger_entry_factory(
            provider_account_id=paid_target.provider_account_id,
            service_id=paid_target.service_id,
            invocation_id=invocation_id,
            payment_attempt_id=attempt_id,
            entry_type=entry_type,
            amount_minor=amount_minor,
        )

    outcome = await run_replayed_paid_invoke(
        invocation_id=invocation_id,
        account_id=paid_target.consumer_account_id,
    )

    attempt = await load_payment_attempt()
    money = await load_money_rows()
    assert "PAYMENT-RESPONSE" in outcome.response_headers
    assert attempt.status is PaymentAttemptStatus.CONSUMED
    assert attempt.invocation_id == invocation_id
    assert len(money.ledger_entries) == 3
    assert len(money.payouts) == 1


async def test_two_concurrent_replays_record_the_money_once(
    paid_target: PaidTarget,
    run_replayed_paid_invoke: ReplayedPaidInvokeRunner,
    payment_attempt_factory: PaymentAttemptFactory,
    invocation_factory: InvocationFactory,
    load_payment_attempt: PaymentAttemptLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    invocation_id = await invocation_factory(
        consumer_account_id=paid_target.consumer_account_id,
        service_id=paid_target.service_id,
        endpoint_id=paid_target.endpoint_id,
        endpoint_key=PAID_ENDPOINT_KEY,
        access_mode=AccessMode.PAID,
        quote_id=paid_target.quote_id,
        payload=PAID_INVOKE_PAYLOAD,
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
    )
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        invocation_id=None,
        status=PaymentAttemptStatus.SETTLED,
        settle_outcome=build_settle_outcome().model_dump(mode="json"),
    )

    first, second = await asyncio.gather(
        run_replayed_paid_invoke(
            invocation_id=invocation_id,
            account_id=paid_target.consumer_account_id,
        ),
        run_replayed_paid_invoke(
            invocation_id=invocation_id,
            account_id=paid_target.consumer_account_id,
        ),
    )

    attempt = await load_payment_attempt()
    money = await load_money_rows()
    assert "PAYMENT-RESPONSE" in first.response_headers
    assert "PAYMENT-RESPONSE" in second.response_headers
    assert attempt.status is PaymentAttemptStatus.CONSUMED
    assert len(money.ledger_entries) == 3
    assert len(money.payouts) == 1


async def test_two_workers_racing_the_settle_claim_settle_once(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    payment_attempt_factory: PaymentAttemptFactory,
    load_payment_attempt: PaymentAttemptLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        status=PaymentAttemptStatus.VERIFIED,
        verify_outcome=VERIFY_ACCEPTED.model_dump(mode="json"),
    )
    settle_started = asyncio.Event()
    release_settle = asyncio.Event()

    async def hold_the_settle(session_factory: async_sessionmaker[AsyncSession]) -> None:
        settle_started.set()
        await release_settle.wait()

    async def run_losing_worker() -> PaymentRequiredChallenge | PaidInvokeSuccess:
        await settle_started.wait()
        try:
            return await run_paid_invoke(
                target=paid_target,
                facilitator_client=never_called_facilitator_client,
            )
        finally:
            release_settle.set()

    facilitator_client = scripted_facilitator_client(
        settle_results=[build_settle_outcome()],
        on_settle=hold_the_settle,
    )

    winning_worker = asyncio.create_task(
        run_paid_invoke(
            target=paid_target,
            facilitator_client=facilitator_client,
            http_client=ScriptedHttpClient(
                responses=[Response(status_code=200, json={"ok": True})],
            ),
        ),
    )
    losing_worker = asyncio.create_task(run_losing_worker())
    try:
        async with asyncio.timeout(RACE_TIMEOUT_SECONDS):
            winner, loser = await asyncio.gather(
                winning_worker,
                losing_worker,
                return_exceptions=True,
            )
    finally:
        winning_worker.cancel()
        losing_worker.cancel()
        await asyncio.gather(winning_worker, losing_worker, return_exceptions=True)

    attempt = await load_payment_attempt()
    money = await load_money_rows()
    assert isinstance(winner, PaidInvokeSuccess)
    assert isinstance(loser, ConflictError)
    assert "in progress" in str(loser)
    assert len(facilitator_client.settle_calls) == 1
    assert attempt.status is PaymentAttemptStatus.CONSUMED
    assert len(money.ledger_entries) == 3
    assert len(money.payouts) == 1


async def test_a_new_payment_cannot_replace_an_attempt_whose_settlement_is_unknown(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    load_payment_attempts: PaymentAttemptsLoader,
) -> None:
    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[FacilitatorTimeoutError("facilitator settle failed: timed out")],
    )

    with pytest.raises(UpstreamError):
        await run_paid_invoke(target=paid_target, facilitator_client=facilitator_client)

    # The same request, signed again. Its first payment may already have moved the
    # payer's funds, so a second one must not be able to move them again.
    with pytest.raises(ConflictError, match="recovery required"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
            payment_identifier="payment-2",
        )

    attempts = await load_payment_attempts()
    assert [attempt.payment_identifier for attempt in attempts] == ["payment-1"]
    assert attempts[0].status is PaymentAttemptStatus.SETTLEMENT_UNKNOWN
    assert len(facilitator_client.settle_calls) == 1


async def test_a_new_payment_cannot_replace_an_attempt_that_is_settling(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    payment_attempt_factory: PaymentAttemptFactory,
    load_payment_attempts: PaymentAttemptsLoader,
) -> None:
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        status=PaymentAttemptStatus.SETTLING,
        settle_in_progress_until=datetime.now(UTC) + timedelta(minutes=5),
    )

    with pytest.raises(ConflictError, match="payment settlement in progress"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
            payment_identifier="payment-2",
        )

    attempts = await load_payment_attempts()
    assert [attempt.payment_identifier for attempt in attempts] == ["payment-1"]


async def test_a_new_payment_can_replace_a_rejected_attempt(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    payment_attempt_factory: PaymentAttemptFactory,
    load_payment_attempts: PaymentAttemptsLoader,
) -> None:
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        status=PaymentAttemptStatus.VERIFY_FAILED,
    )
    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[build_settle_outcome()],
    )

    outcome = await run_paid_invoke(
        target=paid_target,
        facilitator_client=facilitator_client,
        http_client=ScriptedHttpClient(responses=[Response(status_code=200, json={"ok": True})]),
        payment_identifier="payment-2",
    )

    attempts = await load_payment_attempts()
    assert isinstance(outcome, PaidInvokeSuccess)
    assert [attempt.payment_identifier for attempt in attempts] == ["payment-1", "payment-2"]
    assert attempts[1].status is PaymentAttemptStatus.CONSUMED


async def test_two_distinct_payments_racing_for_one_request_settle_once(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    scripted_facilitator_client: ScriptedFacilitatorFactory,
    load_payment_attempts: PaymentAttemptsLoader,
    load_money_rows: MoneyRowsLoader,
) -> None:
    facilitator_client = scripted_facilitator_client(
        verify_results=[VERIFY_ACCEPTED],
        settle_results=[build_settle_outcome()],
    )
    first_http_client = ScriptedHttpClient(
        responses=[Response(status_code=200, json={"ok": True})],
    )
    second_http_client = ScriptedHttpClient(
        responses=[Response(status_code=200, json={"ok": True})],
    )

    # Either worker may win, so both carry everything a winner needs and the scripted
    # facilitator has exactly one verify and one settle to give away.
    first_worker = asyncio.create_task(
        run_paid_invoke(
            target=paid_target,
            facilitator_client=facilitator_client,
            http_client=first_http_client,
            payment_identifier="payment-1",
        ),
    )
    second_worker = asyncio.create_task(
        run_paid_invoke(
            target=paid_target,
            facilitator_client=facilitator_client,
            http_client=second_http_client,
            payment_identifier="payment-2",
        ),
    )
    try:
        async with asyncio.timeout(RACE_TIMEOUT_SECONDS):
            outcomes = await asyncio.gather(
                first_worker,
                second_worker,
                return_exceptions=True,
            )
    finally:
        first_worker.cancel()
        second_worker.cancel()
        await asyncio.gather(first_worker, second_worker, return_exceptions=True)

    attempts = await load_payment_attempts()
    money = await load_money_rows()
    assert sum(isinstance(outcome, PaidInvokeSuccess) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, ConflictError) for outcome in outcomes) == 1
    assert len(attempts) == 1
    assert attempts[0].status is PaymentAttemptStatus.CONSUMED
    assert len(facilitator_client.settle_calls) == 1
    assert len(first_http_client.calls) + len(second_http_client.calls) == 1
    assert len(money.ledger_entries) == 3
    assert len(money.payouts) == 1


async def test_a_payment_identifier_bound_to_another_consumer_is_refused(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    consumer_account_factory: ConsumerAccountFactory,
    payment_attempt_factory: PaymentAttemptFactory,
) -> None:
    other_consumer_account_id = await consumer_account_factory(display_name="Other Consumer")
    await payment_attempt_factory(
        consumer_account_id=other_consumer_account_id,
        quote_id=paid_target.quote_id,
    )

    with pytest.raises(ConflictError, match="payment identifier already used"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
        )


async def test_a_payment_identifier_bound_to_another_quote_is_refused(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    quote_factory: QuoteFactory,
    payment_attempt_factory: PaymentAttemptFactory,
) -> None:
    other_quote_id = await quote_factory(
        service_id=paid_target.service_id,
        endpoint_id=paid_target.endpoint_id,
        endpoint_key=PAID_ENDPOINT_KEY,
        payload=PAID_INVOKE_PAYLOAD,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
    )
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=other_quote_id,
    )

    with pytest.raises(ConflictError, match="payment identifier already used"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
        )


async def test_a_payment_identifier_bound_to_another_idempotency_key_is_refused(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    payment_attempt_factory: PaymentAttemptFactory,
) -> None:
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        idempotency_key="another-invoke-key",
    )

    with pytest.raises(ConflictError, match="payment identifier already used"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
        )


async def test_a_replay_whose_attempt_is_bound_to_another_quote_requires_recovery(
    paid_target: PaidTarget,
    run_replayed_paid_invoke: ReplayedPaidInvokeRunner,
    quote_factory: QuoteFactory,
    payment_attempt_factory: PaymentAttemptFactory,
    invocation_factory: InvocationFactory,
) -> None:
    other_quote_id = await quote_factory(
        service_id=paid_target.service_id,
        endpoint_id=paid_target.endpoint_id,
        endpoint_key=PAID_ENDPOINT_KEY,
        payload=PAID_INVOKE_PAYLOAD,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
    )
    invocation_id = await invocation_factory(
        consumer_account_id=paid_target.consumer_account_id,
        service_id=paid_target.service_id,
        endpoint_id=paid_target.endpoint_id,
        endpoint_key=PAID_ENDPOINT_KEY,
        access_mode=AccessMode.PAID,
        quote_id=paid_target.quote_id,
        payload=PAID_INVOKE_PAYLOAD,
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
    )
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=other_quote_id,
        invocation_id=None,
        status=PaymentAttemptStatus.SETTLED,
        settle_outcome=build_settle_outcome().model_dump(mode="json"),
    )

    with pytest.raises(ConflictError, match="requires recovery"):
        await run_replayed_paid_invoke(
            invocation_id=invocation_id,
            account_id=paid_target.consumer_account_id,
        )


async def test_a_settled_attempt_whose_invocation_is_unresolved_requires_recovery(
    paid_target: PaidTarget,
    run_paid_invoke: PaidInvokeRunner,
    never_called_facilitator_client: NeverCalledFacilitatorClient,
    payment_attempt_factory: PaymentAttemptFactory,
    invocation_factory: InvocationFactory,
    load_payment_attempt: PaymentAttemptLoader,
) -> None:
    await invocation_factory(
        consumer_account_id=paid_target.consumer_account_id,
        service_id=paid_target.service_id,
        endpoint_id=paid_target.endpoint_id,
        endpoint_key=PAID_ENDPOINT_KEY,
        access_mode=AccessMode.PAID,
        quote_id=paid_target.quote_id,
        payload=PAID_INVOKE_PAYLOAD,
        status=InvocationStatus.IN_PROGRESS,
        upstream_status_code=None,
        in_progress_until=EXPIRED_LEASE,
    )
    await payment_attempt_factory(
        consumer_account_id=paid_target.consumer_account_id,
        quote_id=paid_target.quote_id,
        status=PaymentAttemptStatus.SETTLED,
        settle_outcome=build_settle_outcome().model_dump(mode="json"),
    )

    with pytest.raises(ConflictError, match="unknown"):
        await run_paid_invoke(
            target=paid_target,
            facilitator_client=never_called_facilitator_client,
        )

    attempt = await load_payment_attempt()
    assert attempt.status is PaymentAttemptStatus.SETTLED
