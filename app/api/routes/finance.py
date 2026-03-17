from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor, CurrentJwtActor
from app.core.enums import PayoutStatus
from app.core.lifespan import get_app_state
from app.db.session import get_db_session
from app.integrations.payouts import SupportsPayoutExecutor
from app.schemas.finance import (
    ProviderEarningsSummaryResponse,
    ProviderEarningsTotalResponse,
    ProviderLedgerEntryResponse,
    ProviderLedgerResponse,
    ProviderPayoutListResponse,
    ProviderPayoutRequestResponse,
    ProviderPayoutResponse,
    ProviderPayoutSummaryResponse,
)
from app.services.ledger_service import LedgerService
from app.services.payout_service import (
    PayoutConflictError,
    PayoutExecutionService,
    PayoutReportingService,
)

router = APIRouter(prefix="/provider", tags=["finance"])


def _get_payout_executor(request: Request) -> SupportsPayoutExecutor | None:
    payout_executor = get_app_state(request.app).payout_executor
    if payout_executor is None:
        return None
    if not isinstance(payout_executor, SupportsPayoutExecutor):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="payout executor is not initialized",
        )
    return payout_executor


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
    service = PayoutReportingService(session)
    payouts = await service.get_provider_payouts(actor, status=payout_status)
    summaries = await service.get_provider_payout_summaries(actor, status=payout_status)
    return ProviderPayoutListResponse(
        summaries=[
            ProviderPayoutSummaryResponse.from_values(
                currency=summary.currency,
                total_count=summary.total_count,
                ready_count=summary.ready_count,
                pending_count=summary.pending_count,
                sent_count=summary.sent_count,
                failed_count=summary.failed_count,
                total_amount_minor=summary.total_amount_minor,
                sent_amount_minor=summary.sent_amount_minor,
            )
            for summary in summaries
        ],
        payouts=[ProviderPayoutResponse.from_model(payout) for payout in payouts],
    )


@router.post("/payouts", response_model=ProviderPayoutRequestResponse)
async def request_provider_payouts(
    actor: CurrentJwtActor,
    fastapi_request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> ProviderPayoutRequestResponse:
    service = PayoutExecutionService(
        session,
        payout_executor=_get_payout_executor(fastapi_request),
    )
    try:
        result = await service.request_provider_payouts(actor, idempotency_key=idempotency_key)
    except PayoutConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ProviderPayoutRequestResponse.from_values(
        idempotency_key=result.idempotency_key,
        requested_count=result.requested_count,
        sent_count=result.sent_count,
        failed_count=result.failed_count,
        payouts=[ProviderPayoutResponse.from_model(payout) for payout in result.payouts],
    )
