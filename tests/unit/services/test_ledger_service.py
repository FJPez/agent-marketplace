import pytest

from app.core.actor import ActorContext
from app.core.enums import LedgerEntryType
from app.db.models import LedgerEntry
from app.repositories.ledger_entry_repo import LedgerSummary
from app.services.identity_errors import IdentityNotFoundError
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


class FakeProviderProfileRepo:
    def __init__(self, exists: bool = True) -> None:
        self._exists = exists

    async def get_by_account_id(self, account_id: int) -> object | None:
        assert account_id == 11
        return object() if self._exists else None


@pytest.mark.asyncio
async def test_record_paid_invocation_writes_charge_fee_and_provider_earning_entries() -> None:
    repo = FakeLedgerRepo()
    service = LedgerService(
        session=None,
        ledger_repo=repo,
        provider_profile_repo=FakeProviderProfileRepo(),
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
async def test_get_provider_earnings_requires_existing_provider_profile() -> None:
    service = LedgerService(
        session=None,
        ledger_repo=FakeLedgerRepo(),
        provider_profile_repo=FakeProviderProfileRepo(exists=False),
    )

    with pytest.raises(IdentityNotFoundError, match="provider profile"):
        await service.get_provider_earnings(ActorContext(account_id=11))
