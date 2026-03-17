from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.core.enums import LedgerEntryType
from app.core.logging import (
    INVOCATION_ID_FIELD,
    PAYMENT_ATTEMPT_ID_FIELD,
    PROVIDER_ACCOUNT_ID_FIELD,
    SERVICE_ID_FIELD,
    build_event_context,
    get_logger,
)
from app.repositories.ledger_entry_repo import LedgerEntryRepository, LedgerSummary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.db.models import LedgerEntry

PLATFORM_FEE_BPS = 1000
BPS_DENOMINATOR = 10_000
logger = get_logger(__name__)


def split_paid_invocation_amount(amount_minor: int) -> tuple[int, int]:
    platform_fee_minor = (amount_minor * PLATFORM_FEE_BPS) // BPS_DENOMINATOR
    provider_earning_minor = amount_minor - platform_fee_minor
    return platform_fee_minor, provider_earning_minor


class LedgerStore(Protocol):
    def add(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        entry_type: LedgerEntryType,
        amount_minor: int,
        currency: str,
    ) -> object: ...

    async def list_for_provider(self, *, provider_account_id: int) -> list[LedgerEntry]: ...

    async def summarize_for_provider(
        self,
        *,
        provider_account_id: int,
    ) -> list[LedgerSummary]: ...


class LedgerService:
    def __init__(
        self,
        session: AsyncSession | None,
        *,
        ledger_repo: LedgerStore | None = None,
    ) -> None:
        self._session = session
        if ledger_repo is None and session is None:
            msg = "session is required when repositories are not provided"
            raise RuntimeError(msg)
        if ledger_repo is None:
            assert session is not None
            self._ledger_repo = LedgerEntryRepository(session)
        else:
            self._ledger_repo = ledger_repo

    async def record_paid_invocation(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        amount_minor: int,
        currency: str,
    ) -> None:
        platform_fee_minor, provider_earning_minor = split_paid_invocation_amount(amount_minor)
        for entry_type, entry_amount in (
            (LedgerEntryType.CHARGE, amount_minor),
            (LedgerEntryType.PLATFORM_FEE, platform_fee_minor),
            (LedgerEntryType.PROVIDER_EARNING, provider_earning_minor),
        ):
            self._ledger_repo.add(
                provider_account_id=provider_account_id,
                service_id=service_id,
                invocation_id=invocation_id,
                payment_attempt_id=payment_attempt_id,
                entry_type=entry_type,
                amount_minor=entry_amount,
                currency=currency,
            )
        logger.info(
            "ledger entries recorded",
            extra=build_event_context(
                "ledger.recorded",
                **{
                    PROVIDER_ACCOUNT_ID_FIELD: provider_account_id,
                    SERVICE_ID_FIELD: service_id,
                    INVOCATION_ID_FIELD: invocation_id,
                    PAYMENT_ATTEMPT_ID_FIELD: payment_attempt_id,
                },
            ),
        )

    async def get_provider_ledger(self, actor: ActorContext) -> list[LedgerEntry]:
        return await self._ledger_repo.list_for_provider(provider_account_id=actor.account_id)

    async def get_provider_earnings(self, actor: ActorContext) -> list[LedgerSummary]:
        return await self._ledger_repo.summarize_for_provider(provider_account_id=actor.account_id)
