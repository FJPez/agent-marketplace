from typing import Self

from pydantic import BaseModel

from app.db.models import LedgerEntry
from app.repositories.ledger_entry_repo import LedgerSummary
from app.schemas.common import Id, Timestamp


class ProviderLedgerEntryResponse(BaseModel):
    id: Id
    service_id: Id
    invocation_id: Id
    payment_attempt_id: Id
    entry_type: str
    amount_minor: int
    currency: str
    created_at: Timestamp

    @classmethod
    def from_model(cls, entry: LedgerEntry) -> Self:
        return cls(
            id=entry.id,
            service_id=entry.service_id,
            invocation_id=entry.invocation_id,
            payment_attempt_id=entry.payment_attempt_id,
            entry_type=entry.entry_type.value,
            amount_minor=entry.amount_minor,
            currency=entry.currency,
            created_at=entry.created_at,
        )


class ProviderLedgerResponse(BaseModel):
    entries: list[ProviderLedgerEntryResponse]


class ProviderEarningsTotalResponse(BaseModel):
    currency: str
    charge_minor: int
    platform_fee_minor: int
    provider_earning_minor: int
    entry_count: int

    @classmethod
    def from_summary(cls, summary: LedgerSummary) -> Self:
        return cls(
            currency=summary.currency,
            charge_minor=summary.charge_minor,
            platform_fee_minor=summary.platform_fee_minor,
            provider_earning_minor=summary.provider_earning_minor,
            entry_count=summary.entry_count,
        )


class ProviderEarningsSummaryResponse(BaseModel):
    totals: list[ProviderEarningsTotalResponse]
