from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest

from app.core.actor import ActorContext
from app.core.enums import PayoutFailureCode, PayoutStatus
from app.integrations.payouts.executor import PreparedPayout, SentPayout
from app.services.payout_service import (
    AccountStore,
    PayoutConflictError,
    PayoutExecutionService,
    PayoutExecutionStore,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class FakePayout:
    id: int
    provider_account_id: int
    service_id: int
    invocation_id: int
    payment_attempt_id: int
    destination_wallet: str | None
    amount_minor: int
    currency: str
    network: str
    status: PayoutStatus
    attempt_count: int
    request_idempotency_key: str | None = None
    failure_code: PayoutFailureCode | None = None
    error_message: str | None = None
    transfer_reference: str | None = None
    prepared_raw_transaction: str | None = None
    chain_nonce: int | None = None


class FakeSession:
    def __init__(self) -> None:
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


class FakePayoutRepository:
    def __init__(self) -> None:
        self.payouts: list[FakePayout] = []
        self.claim_treasury_lock_calls = 0

    def add(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        destination_wallet: str | None,
        amount_minor: int,
        currency: str,
        network: str,
        status: PayoutStatus,
        transfer_reference: str | None = None,
        request_idempotency_key: str | None = None,
        failure_code: PayoutFailureCode | None = None,
        error_message: str | None = None,
        attempt_count: int = 1,
        prepared_raw_transaction: str | None = None,
        chain_nonce: int | None = None,
    ) -> FakePayout:
        payout = FakePayout(
            id=len(self.payouts) + 1,
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet=destination_wallet,
            amount_minor=amount_minor,
            currency=currency,
            network=network,
            status=status,
            attempt_count=attempt_count,
            request_idempotency_key=request_idempotency_key,
            failure_code=failure_code,
            error_message=error_message,
            transfer_reference=transfer_reference,
            prepared_raw_transaction=prepared_raw_transaction,
            chain_nonce=chain_nonce,
        )
        self.payouts.append(payout)
        return payout

    async def get_by_payment_attempt_id(self, *, payment_attempt_id: int) -> FakePayout | None:
        return next(
            (payout for payout in self.payouts if payout.payment_attempt_id == payment_attempt_id),
            None,
        )

    async def claim_treasury_lock(self) -> None:
        self.claim_treasury_lock_calls += 1

    async def list_for_provider_request(
        self,
        *,
        provider_account_id: int,
        request_idempotency_key: str,
        for_update: bool = False,
    ) -> list[FakePayout]:
        _ = for_update
        payouts = [
            payout
            for payout in self.payouts
            if payout.provider_account_id == provider_account_id
            and payout.request_idempotency_key == request_idempotency_key
        ]
        return list(payouts)

    async def list_in_flight_for_provider(self, *, provider_account_id: int) -> list[FakePayout]:
        return [
            payout
            for payout in self.payouts
            if payout.provider_account_id == provider_account_id
            and payout.status is PayoutStatus.PENDING
        ]

    async def claim_ready_for_provider(self, *, provider_account_id: int) -> list[FakePayout]:
        return [
            payout
            for payout in self.payouts
            if payout.provider_account_id == provider_account_id
            and payout.status is PayoutStatus.READY
            and payout.request_idempotency_key is None
        ]

    async def get_max_claimed_chain_nonce(self) -> int | None:
        nonces = [
            payout.chain_nonce
            for payout in self.payouts
            if payout.chain_nonce is not None
            and payout.status in {PayoutStatus.PENDING, PayoutStatus.SENT}
        ]
        if not nonces:
            return None
        return max(nonces)


class FakeAccountRepository:
    def __init__(self, wallet_address: str | None) -> None:
        self.wallet_address = wallet_address

    async def get(self, account_id: int) -> object | None:
        _ = account_id
        if self.wallet_address is None:
            return None
        return type("AccountStub", (), {"wallet_address": self.wallet_address})()


class FakePayoutExecutor:
    def __init__(
        self,
        *,
        current_nonce: int = 0,
        fail_send: bool = False,
        fail_prepare: bool = False,
    ) -> None:
        self._current_nonce = current_nonce
        self._fail_send = fail_send
        self._fail_prepare = fail_prepare
        self.prepare_calls: list[dict[str, object]] = []
        self.send_calls: list[dict[str, object]] = []

    async def current_nonce(self) -> int:
        return self._current_nonce

    async def prepare_payout(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
        idempotency_key: str,
        nonce: int,
    ) -> PreparedPayout:
        self.prepare_calls.append(
            {
                "destination_wallet": destination_wallet,
                "amount_minor": amount_minor,
                "idempotency_key": idempotency_key,
                "nonce": nonce,
            }
        )
        if self._fail_prepare:
            from app.integrations.payouts.executor import PayoutExecutionError

            raise PayoutExecutionError("rpc unavailable")
        return PreparedPayout(
            raw_transaction=f"0xraw{nonce}",
            reference=f"0xref{nonce}",
            network="base-sepolia",
            token_address="0x" + "ab" * 20,
        )

    async def send_prepared_payout(
        self,
        *,
        raw_transaction: str,
        reference: str,
    ) -> SentPayout:
        self.send_calls.append(
            {
                "raw_transaction": raw_transaction,
                "reference": reference,
            }
        )
        if self._fail_send:
            raise RuntimeError("rpc unavailable")
        return SentPayout(
            reference=reference,
            network="base-sepolia",
            token_address="0x" + "ab" * 20,
        )


def _actor(account_id: int = 1) -> ActorContext:
    return ActorContext(account_id=account_id)


@pytest.mark.asyncio
async def test_record_ready_payout_returns_existing_payment_attempt_match() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    existing = payout_repo.add(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        destination_wallet=None,
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.READY,
        attempt_count=0,
    )
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
    )

    payout = await service.record_ready_payout(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        gross_amount_minor=500,
        currency="USDC",
        network="base-sepolia",
    )

    assert payout is existing
    assert session.flush_calls == 0


@pytest.mark.asyncio
async def test_record_ready_payout_persists_provider_share_as_ready_row() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
    )

    payout = await service.record_ready_payout(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        gross_amount_minor=500,
        currency="USDC",
        network="base-sepolia",
    )

    assert session.flush_calls == 1
    assert session.commit_calls == 0
    assert payout.amount_minor == 450
    assert payout.status is PayoutStatus.READY
    assert payout.destination_wallet is None
    assert payout.attempt_count == 0


@pytest.mark.asyncio
async def test_record_ready_payout_marks_invalid_amount_failed() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
    )

    payout = await service.record_ready_payout(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        gross_amount_minor=0,
        currency="USDC",
        network="base-sepolia",
    )

    assert payout.amount_minor == 0
    assert payout.status is PayoutStatus.FAILED
    assert payout.failure_code is PayoutFailureCode.INVALID_AMOUNT
    assert payout.error_message == "provider payout amount must be positive"


@pytest.mark.asyncio
async def test_request_provider_payouts_claims_ready_rows_and_sends_batch() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    payout_repo.add(
        provider_account_id=1,
        service_id=10,
        invocation_id=100,
        payment_attempt_id=1000,
        destination_wallet="0x00000000000000000000000000000000000000bb",
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.SENT,
        chain_nonce=11,
    )
    payout_repo.add(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        destination_wallet=None,
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.READY,
        attempt_count=0,
    )
    payout_repo.add(
        provider_account_id=1,
        service_id=2,
        invocation_id=5,
        payment_attempt_id=6,
        destination_wallet=None,
        amount_minor=900,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.READY,
        attempt_count=0,
    )
    executor = FakePayoutExecutor(current_nonce=9)
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
        account_repo=cast(
            "AccountStore",
            FakeAccountRepository("0x00000000000000000000000000000000000000aa"),
        ),
        payout_executor=executor,
    )

    result = await service.request_provider_payouts(_actor(), idempotency_key="payout-request-1")

    assert result.idempotency_key == "payout-request-1"
    assert result.requested_count == 2
    assert result.sent_count == 2
    assert result.failed_count == 0
    assert payout_repo.claim_treasury_lock_calls == 1
    assert session.flush_calls == 1
    assert session.commit_calls == 3
    assert session.rollback_calls == 0
    assert executor.prepare_calls == [
        {
            "destination_wallet": "0x00000000000000000000000000000000000000aa",
            "amount_minor": 450,
            "idempotency_key": "payout-request-1",
            "nonce": 12,
        },
        {
            "destination_wallet": "0x00000000000000000000000000000000000000aa",
            "amount_minor": 900,
            "idempotency_key": "payout-request-1",
            "nonce": 13,
        },
    ]
    assert executor.send_calls == [
        {
            "raw_transaction": "0xraw12",
            "reference": "0xref12",
        },
        {
            "raw_transaction": "0xraw13",
            "reference": "0xref13",
        },
    ]
    assert [payout.status for payout in result.payouts] == [PayoutStatus.SENT, PayoutStatus.SENT]


@pytest.mark.asyncio
async def test_request_provider_payouts_replays_existing_terminal_batch() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    payout_repo.add(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        destination_wallet="0x00000000000000000000000000000000000000aa",
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.SENT,
        request_idempotency_key="payout-request-1",
        transfer_reference="0xref12",
    )
    executor = FakePayoutExecutor(current_nonce=9)
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
        account_repo=cast(
            "AccountStore",
            FakeAccountRepository("0x00000000000000000000000000000000000000aa"),
        ),
        payout_executor=executor,
    )

    result = await service.request_provider_payouts(_actor(), idempotency_key="payout-request-1")

    assert result.requested_count == 1
    assert executor.prepare_calls == []
    assert executor.send_calls == []
    assert session.commit_calls == 0
    assert session.rollback_calls == 1


@pytest.mark.asyncio
async def test_request_provider_payouts_rejects_same_key_pending_batch() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    payout_repo.add(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        destination_wallet="0x00000000000000000000000000000000000000aa",
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.PENDING,
        request_idempotency_key="payout-request-1",
        attempt_count=1,
        prepared_raw_transaction="0xraw12",
        transfer_reference="0xref12",
        chain_nonce=12,
    )
    executor = FakePayoutExecutor(current_nonce=99)
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
        account_repo=cast(
            "AccountStore",
            FakeAccountRepository("0x00000000000000000000000000000000000000aa"),
        ),
        payout_executor=executor,
    )

    with pytest.raises(PayoutConflictError, match="provider payout batch already in progress"):
        await service.request_provider_payouts(_actor(), idempotency_key="payout-request-1")

    assert executor.prepare_calls == []
    assert executor.send_calls == []
    assert session.commit_calls == 0
    assert session.rollback_calls == 1


@pytest.mark.asyncio
async def test_request_provider_payouts_rejects_other_in_flight_batch() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    payout_repo.add(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        destination_wallet="0x00000000000000000000000000000000000000aa",
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.PENDING,
        request_idempotency_key="other-request",
        attempt_count=1,
        prepared_raw_transaction="0xraw12",
        transfer_reference="0xref12",
        chain_nonce=12,
    )
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
        account_repo=cast(
            "AccountStore",
            FakeAccountRepository("0x00000000000000000000000000000000000000aa"),
        ),
        payout_executor=FakePayoutExecutor(current_nonce=99),
    )

    with pytest.raises(PayoutConflictError, match="provider payout batch already in progress"):
        await service.request_provider_payouts(_actor(), idempotency_key="payout-request-1")

    assert session.rollback_calls == 1


@pytest.mark.asyncio
async def test_request_provider_payouts_rejects_missing_wallet() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    payout_repo.add(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        destination_wallet=None,
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.READY,
        attempt_count=0,
    )
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
        account_repo=cast("AccountStore", FakeAccountRepository(None)),
        payout_executor=FakePayoutExecutor(current_nonce=9),
    )

    with pytest.raises(PayoutConflictError, match="provider wallet address is not configured"):
        await service.request_provider_payouts(_actor(), idempotency_key="payout-request-1")


@pytest.mark.asyncio
async def test_request_provider_payouts_leaves_ready_rows_on_prepare_failure() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    payout_repo.add(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        destination_wallet=None,
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.READY,
        attempt_count=0,
    )
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
        account_repo=cast(
            "AccountStore",
            FakeAccountRepository("0x00000000000000000000000000000000000000aa"),
        ),
        payout_executor=FakePayoutExecutor(current_nonce=9, fail_prepare=True),
    )

    with pytest.raises(PayoutConflictError, match="payout could not be prepared"):
        await service.request_provider_payouts(_actor(), idempotency_key="payout-request-1")

    assert payout_repo.payouts[0].status is PayoutStatus.READY
    assert payout_repo.payouts[0].request_idempotency_key is None
    assert session.rollback_calls == 1


@pytest.mark.asyncio
async def test_request_provider_payouts_marks_rows_failed_on_send_failure() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    payout_repo.add(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        destination_wallet=None,
        amount_minor=450,
        currency="USDC",
        network="base-sepolia",
        status=PayoutStatus.READY,
        attempt_count=0,
    )
    service = PayoutExecutionService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutExecutionStore", payout_repo),
        account_repo=cast(
            "AccountStore",
            FakeAccountRepository("0x00000000000000000000000000000000000000aa"),
        ),
        payout_executor=FakePayoutExecutor(current_nonce=9, fail_send=True),
    )

    result = await service.request_provider_payouts(_actor(), idempotency_key="payout-request-1")

    assert result.payouts[0].status is PayoutStatus.FAILED
    assert result.payouts[0].failure_code is PayoutFailureCode.EXECUTOR_ERROR
    assert result.payouts[0].prepared_raw_transaction == "0xraw9"
