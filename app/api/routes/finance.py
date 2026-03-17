from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.core.enums import PayoutStatus
from app.db.session import get_db_session
from app.schemas.finance import (
    ProviderEarningsSummaryResponse,
    ProviderEarningsTotalResponse,
    ProviderLedgerEntryResponse,
    ProviderLedgerResponse,
    ProviderPayoutListResponse,
    ProviderPayoutResponse,
    ProviderPayoutSummaryResponse,
)
from app.services.ledger_service import LedgerService
from app.services.payout_service import PayoutService

router = APIRouter(prefix="/provider", tags=["finance"])


@router.get("/earnings", response_model=ProviderEarningsSummaryResponse)
async def get_provider_earnings(
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderEarningsSummaryResponse:
    service = LedgerService(session)
    totals = await service.get_provider_earnings(actor)
    return ProviderEarningsSummaryResponse(
        totals=[ProviderEarningsTotalResponse.from_summary(total) for total in totals],
    )


@router.get("/ledger", response_model=ProviderLedgerResponse)
async def get_provider_ledger(
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderLedgerResponse:
    service = LedgerService(session)
    entries = await service.get_provider_ledger(actor)
    return ProviderLedgerResponse(
        entries=[ProviderLedgerEntryResponse.from_model(entry) for entry in entries],
    )


@router.get("/payouts", response_model=ProviderPayoutListResponse)
async def get_provider_payouts(
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    payout_status: Annotated[PayoutStatus | None, Query(alias="status")] = None,
) -> ProviderPayoutListResponse:
    service = PayoutService(session)
    payouts = await service.get_provider_payouts(actor, status=payout_status)
    summary = await service.get_provider_payout_summary(actor)
    return ProviderPayoutListResponse(
        summary=None if summary is None else ProviderPayoutSummaryResponse.from_summary(summary),
        payouts=[ProviderPayoutResponse.from_model(payout) for payout in payouts],
    )
