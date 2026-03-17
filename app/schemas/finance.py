from typing import Self

from pydantic import BaseModel

from app.core.enums import PayoutStatus
from app.db.models import LedgerEntry, Payout
from app.repositories.ledger_entry_repo import LedgerSummary
from app.repositories.payout_repo import PayoutSummary
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


class ProviderPayoutResponse(BaseModel):
    id: Id
    service_id: Id
    invocation_id: Id
    payment_attempt_id: Id
    destination_wallet: str
    amount_minor: int
    currency: str
    network: str
    status: PayoutStatus
    transfer_reference: str | None
    error_message: str | None
    attempt_count: int
    created_at: Timestamp
    updated_at: Timestamp

    @classmethod
    def from_model(cls, payout: Payout) -> Self:
        sanitized_error = None
        if payout.error_message is not None:
            sanitized_error = payout.error_message[:200]
        return cls(
            id=payout.id,
            service_id=payout.service_id,
            invocation_id=payout.invocation_id,
            payment_attempt_id=payout.payment_attempt_id,
            destination_wallet=payout.destination_wallet,
            amount_minor=payout.amount_minor,
            currency=payout.currency,
            network=payout.network,
            status=payout.status,
            transfer_reference=payout.transfer_reference,
            error_message=sanitized_error,
            attempt_count=payout.attempt_count,
            created_at=payout.created_at,
            updated_at=payout.updated_at,
        )


class ProviderPayoutSummaryResponse(BaseModel):
    currency: str | None
    total_count: int
    ready_count: int
    pending_count: int
    sent_count: int
    failed_count: int
    total_amount_minor: int
    sent_amount_minor: int

    @classmethod
    def from_summary(cls, summary: PayoutSummary) -> Self:
        return cls(
            currency=summary.currency,
            total_count=summary.total_count,
            ready_count=summary.ready_count,
            pending_count=summary.pending_count,
            sent_count=summary.sent_count,
            failed_count=summary.failed_count,
            total_amount_minor=summary.total_amount_minor,
            sent_amount_minor=summary.sent_amount_minor,
        )


class ProviderPayoutListResponse(BaseModel):
    summary: ProviderPayoutSummaryResponse | None
    payouts: list[ProviderPayoutResponse]
