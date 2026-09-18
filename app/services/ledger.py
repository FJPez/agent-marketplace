"""The double-entry record of one settled paid invocation."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import LedgerEntryType
from app.db.models import LedgerEntry

PLATFORM_FEE_BPS = 1000
BPS_DENOMINATOR = 10_000


def split_paid_invocation_amount(amount_minor: int) -> tuple[int, int]:
    """Split a charge into the platform fee and what is left for the provider."""
    platform_fee_minor = (amount_minor * PLATFORM_FEE_BPS) // BPS_DENOMINATOR
    provider_earning_minor = amount_minor - platform_fee_minor
    return platform_fee_minor, provider_earning_minor


async def record_paid_invocation(
    *,
    session: AsyncSession,
    provider_account_id: int,
    service_id: int,
    invocation_id: int,
    payment_attempt_id: int,
    amount_minor: int,
    currency: str,
) -> None:
    """Write the charge, the fee, and the earning for one attempt, leaving the commit to the caller.

    Each entry is inserted at most once per attempt, so a caller repeating the write
    after a crash adds nothing and changes nothing.
    """
    platform_fee_minor, provider_earning_minor = split_paid_invocation_amount(amount_minor)
    for entry_type, entry_amount in (
        (LedgerEntryType.CHARGE, amount_minor),
        (LedgerEntryType.PLATFORM_FEE, platform_fee_minor),
        (LedgerEntryType.PROVIDER_EARNING, provider_earning_minor),
    ):
        await session.execute(
            insert(LedgerEntry)
            .values(
                provider_account_id=provider_account_id,
                service_id=service_id,
                invocation_id=invocation_id,
                payment_attempt_id=payment_attempt_id,
                entry_type=entry_type,
                amount_minor=entry_amount,
                currency=currency,
            )
            .on_conflict_do_nothing(index_elements=["payment_attempt_id", "entry_type"]),
        )
