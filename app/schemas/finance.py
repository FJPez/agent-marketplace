from typing import Self

from pydantic import BaseModel, ConfigDict

from app.core.enums import PayoutFailureCode, PayoutStatus
from app.db.models import LedgerEntry, Payout
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
    model_config = ConfigDict(from_attributes=True)

    currency: str
    charge_minor: int
    platform_fee_minor: int
    provider_earning_minor: int
    entry_count: int


class ProviderEarningsSummaryResponse(BaseModel):
    totals: list[ProviderEarningsTotalResponse]


class ProviderPayoutResponse(BaseModel):
    id: Id
    service_id: Id
    invocation_id: Id
    payment_attempt_id: Id
    destination_wallet: str | None
    amount_minor: int
    currency: str
    network: str
    status: PayoutStatus
    failure_code: PayoutFailureCode | None
    attempt_count: int
    created_at: Timestamp
    updated_at: Timestamp

    @classmethod
    def from_model(cls, payout: Payout) -> Self:
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
            failure_code=payout.failure_code,
            attempt_count=payout.attempt_count,
            created_at=payout.created_at,
            updated_at=payout.updated_at,
        )


class ProviderPayoutSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    currency: str | None
    total_count: int
    ready_count: int
    pending_count: int
    sent_count: int
    failed_count: int
    total_amount_minor: int
    sent_amount_minor: int


class ProviderPayoutListResponse(BaseModel):
    summaries: list[ProviderPayoutSummaryResponse]
    payouts: list[ProviderPayoutResponse]


class ProviderPayoutRequestResponse(BaseModel):
    idempotency_key: str
    requested_count: int
    sent_count: int
    failed_count: int
    payouts: list[ProviderPayoutResponse]

    @classmethod
    def from_values(
        cls,
        *,
        idempotency_key: str,
        requested_count: int,
        sent_count: int,
        failed_count: int,
        payouts: list[ProviderPayoutResponse],
    ) -> Self:
        return cls(
            idempotency_key=idempotency_key,
            requested_count=requested_count,
            sent_count=sent_count,
            failed_count=failed_count,
            payouts=payouts,
        )
