from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.repositories.payout_repo import PayoutRepository, PayoutSummary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.core.enums import PayoutStatus
    from app.db.models import Payout


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
        return await self._payout_repo.list_for_provider(
            provider_account_id=actor.account_id,
            status=status,
        )

    async def get_provider_payout_summary(self, actor: ActorContext) -> PayoutSummary | None:
        return await self._payout_repo.summarize_for_provider(provider_account_id=actor.account_id)
