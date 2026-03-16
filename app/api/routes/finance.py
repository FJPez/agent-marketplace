from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.db.session import get_db_session
from app.schemas.finance import (
    ProviderEarningsSummaryResponse,
    ProviderEarningsTotalResponse,
    ProviderLedgerEntryResponse,
    ProviderLedgerResponse,
)
from app.services.identity_errors import IdentityNotFoundError
from app.services.ledger_service import LedgerService

router = APIRouter(prefix="/provider", tags=["finance"])


def _to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, IdentityNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="provider profile not found",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=str(exc),
    )


@router.get("/earnings", response_model=ProviderEarningsSummaryResponse)
async def get_provider_earnings(
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderEarningsSummaryResponse:
    service = LedgerService(session)
    try:
        totals = await service.get_provider_earnings(actor)
    except IdentityNotFoundError as exc:
        raise _to_http_exception(exc) from exc
    return ProviderEarningsSummaryResponse(
        totals=[ProviderEarningsTotalResponse.from_summary(total) for total in totals],
    )


@router.get("/ledger", response_model=ProviderLedgerResponse)
async def get_provider_ledger(
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderLedgerResponse:
    service = LedgerService(session)
    try:
        entries = await service.get_provider_ledger(actor)
    except IdentityNotFoundError as exc:
        raise _to_http_exception(exc) from exc
    return ProviderLedgerResponse(
        entries=[ProviderLedgerEntryResponse.from_model(entry) for entry in entries],
    )
