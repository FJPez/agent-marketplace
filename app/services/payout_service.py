from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.core.logging import (
    PAYOUT_COUNT_FIELD,
    PAYOUT_STATUS_FIELD,
    PROVIDER_ACCOUNT_ID_FIELD,
    build_event_context,
    get_logger,
)
from app.repositories.payout_repo import PayoutRepository, PayoutSummary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.core.enums import PayoutStatus
    from app.db.models import Payout

logger = get_logger(__name__)


class PayoutStore(Protocol):
    async def list_for_provider(
        self,
        *,
        provider_account_id: int,
        status: PayoutStatus | None = None,
    ) -> list[Payout]: ...

    async def summarize_for_provider(
        self,
        *,
        provider_account_id: int,
    ) -> PayoutSummary | None: ...


class PayoutService:
    def __init__(
        self,
        session: AsyncSession | None,
        *,
        payout_repo: PayoutStore | None = None,
    ) -> None:
        if payout_repo is None and session is None:
            msg = "session is required when repositories are not provided"
            raise RuntimeError(msg)
        if payout_repo is None:
            assert session is not None
            self._payout_repo = PayoutRepository(session)
        else:
            self._payout_repo = payout_repo

    async def get_provider_payouts(
        self,
        actor: ActorContext,
        *,
        status: PayoutStatus | None = None,
    ) -> list[Payout]:
        payouts = await self._payout_repo.list_for_provider(
            provider_account_id=actor.account_id,
            status=status,
        )
        logger.info(
            "provider payouts listed",
            extra=build_event_context(
                "payout.reporting_listed",
                **{
                    PROVIDER_ACCOUNT_ID_FIELD: actor.account_id,
                    PAYOUT_STATUS_FIELD: None if status is None else status.value,
                    PAYOUT_COUNT_FIELD: len(payouts),
                },
            ),
        )
        return payouts

    async def get_provider_payout_summary(self, actor: ActorContext) -> PayoutSummary | None:
        return await self._payout_repo.summarize_for_provider(provider_account_id=actor.account_id)
