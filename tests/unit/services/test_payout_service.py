from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest

from app.core.config import Settings
from app.core.enums import PayoutStatus
from app.services.payout_service import AccountStore, PayoutService, PayoutStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class FakePayout:
    id: int
    status: PayoutStatus
    attempt_count: int
    destination_wallet: str
    error_message: str | None = None
    transfer_reference: str | None = None


class FakeSession:
    def __init__(self) -> None:
        self.flush_calls = 0
        self.commit_calls = 0

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1


class FakePayoutRepository:
    def __init__(self) -> None:
        self.lookup_result: FakePayout | None = None
        self.add_calls: list[dict[str, object]] = []
        self.payouts: list[FakePayout] = []

    async def get_by_payment_attempt_id(self, *, payment_attempt_id: int) -> FakePayout | None:
        _ = payment_attempt_id
        return self.lookup_result

    def add(self, **kwargs: object) -> FakePayout:
        self.add_calls.append(kwargs)
        status = kwargs["status"]
        attempt_count = kwargs.get("attempt_count", 1)
        destination_wallet = kwargs["destination_wallet"]
        error_message = kwargs.get("error_message")
        transfer_reference = kwargs.get("transfer_reference")
        assert isinstance(status, PayoutStatus)
        assert isinstance(attempt_count, int)
        assert isinstance(destination_wallet, str)
        assert error_message is None or isinstance(error_message, str)
        assert transfer_reference is None or isinstance(transfer_reference, str)
        payout = FakePayout(
            id=len(self.payouts) + 1,
            status=status,
            attempt_count=attempt_count,
            destination_wallet=destination_wallet,
            error_message=error_message,
            transfer_reference=transfer_reference,
        )
        self.payouts.append(payout)
        return payout


class FakeAccountRepository:
    def __init__(self, wallet_address: str | None) -> None:
        self.wallet_address = wallet_address

    async def get(self, account_id: int) -> object | None:
        _ = account_id
        if self.wallet_address is None:
            return None
        return type("AccountStub", (), {"wallet_address": self.wallet_address})()


class FakePayoutExecutor:
    def __init__(self, session: FakeSession, repo: FakePayoutRepository) -> None:
        self._session = session
        self._repo = repo
        self.calls: list[dict[str, object]] = []

    async def send_payout(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        assert self._session.commit_calls == 1
        assert self._repo.payouts[0].status is PayoutStatus.PENDING
        self.calls.append(
            {
                "destination_wallet": destination_wallet,
                "amount_minor": amount_minor,
                "idempotency_key": idempotency_key,
            }
        )
        return {"reference": "0xpayoutsent"}


def _settings(*, payouts_enabled: bool) -> Settings:
    return Settings.model_construct(
        payouts_enabled=payouts_enabled,
        x402_network="base-sepolia",
    )


@pytest.mark.asyncio
async def test_record_provider_payout_commits_pending_before_send() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    executor = FakePayoutExecutor(session, payout_repo)
    service = PayoutService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutStore", payout_repo),
        account_repo=cast(
            "AccountStore",
            FakeAccountRepository("0x00000000000000000000000000000000000000aa"),
        ),
        settings=_settings(payouts_enabled=True),
        payout_executor=executor,
    )

    await service.record_provider_payout(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        gross_amount_minor=500,
        currency="USDC",
    )

    assert session.flush_calls == 1
    assert session.commit_calls == 2
    assert payout_repo.add_calls[0]["amount_minor"] == 450
    assert executor.calls == [
        {
            "destination_wallet": "0x00000000000000000000000000000000000000aa",
            "amount_minor": 450,
            "idempotency_key": "payment-attempt:4",
        }
    ]
    assert payout_repo.payouts[0].status is PayoutStatus.SENT
    assert payout_repo.payouts[0].transfer_reference == "0xpayoutsent"
    assert payout_repo.payouts[0].attempt_count == 1


@pytest.mark.asyncio
async def test_record_provider_payout_marks_missing_wallet_failed_and_commits() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    service = PayoutService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutStore", payout_repo),
        account_repo=cast("AccountStore", FakeAccountRepository(None)),
        settings=_settings(payouts_enabled=True),
        payout_executor=None,
    )

    await service.record_provider_payout(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        gross_amount_minor=500,
        currency="USDC",
    )

    assert session.commit_calls == 1
    assert payout_repo.payouts[0].status is PayoutStatus.FAILED
    assert payout_repo.payouts[0].destination_wallet == ""
    assert payout_repo.payouts[0].error_message == "provider wallet address is not configured"


@pytest.mark.asyncio
async def test_record_provider_payout_persists_ready_when_executor_disabled() -> None:
    session = FakeSession()
    payout_repo = FakePayoutRepository()
    service = PayoutService(
        cast("AsyncSession", session),
        payout_repo=cast("PayoutStore", payout_repo),
        account_repo=cast(
            "AccountStore",
            FakeAccountRepository("0x00000000000000000000000000000000000000aa"),
        ),
        settings=_settings(payouts_enabled=False),
        payout_executor=None,
    )

    await service.record_provider_payout(
        provider_account_id=1,
        service_id=2,
        invocation_id=3,
        payment_attempt_id=4,
        gross_amount_minor=500,
        currency="USDC",
    )

    assert session.commit_calls == 1
    assert payout_repo.payouts[0].status is PayoutStatus.READY
    assert payout_repo.payouts[0].attempt_count == 0
