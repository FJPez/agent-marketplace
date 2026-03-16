import pytest

from app.core.actor import ActorContext
from app.core.enums import LedgerEntryType
from app.db.models import LedgerEntry
from app.repositories.ledger_entry_repo import LedgerSummary
from app.services.ledger_service import LedgerService


class FakeLedgerRepo:
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []
        self.summaries = [
            LedgerSummary(
                currency="USD",
                charge_minor=500,
                platform_fee_minor=50,
                provider_earning_minor=450,
                entry_count=3,
            )
        ]

    def add(self, **kwargs: object) -> object:
        self.entries.append(kwargs)
        return object()

    async def list_for_provider(self, *, provider_account_id: int) -> list[LedgerEntry]:
        assert provider_account_id == 11
        return []

    async def summarize_for_provider(
        self,
        *,
        provider_account_id: int,
    ) -> list[LedgerSummary]:
        assert provider_account_id == 11
        return self.summaries


@pytest.mark.asyncio
async def test_record_paid_invocation_writes_charge_fee_and_provider_earning_entries() -> None:
    repo = FakeLedgerRepo()
    service = LedgerService(
        session=None,
        ledger_repo=repo,
    )

    await service.record_paid_invocation(
        provider_account_id=11,
        service_id=21,
        invocation_id=31,
        payment_attempt_id=41,
        amount_minor=500,
        currency="USD",
    )

    assert [entry["entry_type"] for entry in repo.entries] == [
        LedgerEntryType.CHARGE,
        LedgerEntryType.PLATFORM_FEE,
        LedgerEntryType.PROVIDER_EARNING,
    ]
    assert [entry["amount_minor"] for entry in repo.entries] == [500, 50, 450]


@pytest.mark.asyncio
async def test_get_provider_earnings_returns_repository_summary() -> None:
    service = LedgerService(
        session=None,
        ledger_repo=FakeLedgerRepo(),
    )

    summaries = await service.get_provider_earnings(ActorContext(account_id=11))

    assert summaries == [
        LedgerSummary(
            currency="USD",
            charge_minor=500,
            platform_fee_minor=50,
            provider_earning_minor=450,
            entry_count=3,
        )
    ]
