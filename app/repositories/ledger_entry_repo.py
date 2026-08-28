from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.enums import LedgerEntryType
from app.db.models import LedgerEntry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class LedgerEntryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> LedgerEntry:
        entry = LedgerEntry(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            entry_type=entry_type,
            amount_minor=amount_minor,
            currency=currency,
        )
        self._session.add(entry)
        return entry
