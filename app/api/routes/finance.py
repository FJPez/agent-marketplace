from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.deps.auth import CurrentActor, CurrentJwtActor
from app.api.deps.database import SessionDep
from app.api.deps.headers import ValidatedIdempotencyKey
from app.core.enums import PayoutStatus
from app.core.lifespan import get_app_state
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
from app.services import finance
from app.services.payout_service import PayoutExecutionService

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


@router.get(
    "/earnings",
    response_model=ProviderEarningsSummaryResponse,
    summary="Get provider earnings totals",
    description=(
        "Returns aggregated provider earnings derived from settled marketplace ledger entries."
    ),
    responses={200: {"description": "Provider earnings summary returned successfully."}},
)
async def get_provider_earnings(
    actor: CurrentActor,
    session: SessionDep,
) -> ProviderEarningsSummaryResponse:
    totals = await finance.get_provider_earnings(session=session, account_id=actor.account_id)
    return ProviderEarningsSummaryResponse(
        totals=[ProviderEarningsTotalResponse.model_validate(total) for total in totals],
    )


@router.get(
    "/ledger",
    response_model=ProviderLedgerResponse,
    summary="Get provider ledger entries",
    description="Returns provider-visible ledger entries for the authenticated actor.",
    responses={200: {"description": "Provider ledger returned successfully."}},
)
async def get_provider_ledger(
    actor: CurrentActor,
    session: SessionDep,
) -> ProviderLedgerResponse:
    entries = await finance.get_provider_ledger(session=session, account_id=actor.account_id)
    return ProviderLedgerResponse(
        entries=[ProviderLedgerEntryResponse.from_model(entry) for entry in entries],
    )


@router.get(
    "/payouts",
    response_model=ProviderPayoutListResponse,
    summary="List provider payouts",
    description=(
        "Returns payout summaries and payout records for the authenticated provider. "
        "This read route accepts generic bearer auth."
    ),
    responses={200: {"description": "Provider payouts returned successfully."}},
)
async def get_provider_payouts(
    actor: CurrentActor,
    session: SessionDep,
    payout_status: Annotated[PayoutStatus | None, Query(alias="status")] = None,
) -> ProviderPayoutListResponse:
    payouts = await finance.get_provider_payouts(
        session=session,
        account_id=actor.account_id,
        status=payout_status,
    )
    summaries = await finance.get_provider_payout_summaries(
        session=session,
        account_id=actor.account_id,
        status=payout_status,
    )
    return ProviderPayoutListResponse(
        summaries=[ProviderPayoutSummaryResponse.model_validate(summary) for summary in summaries],
        payouts=[ProviderPayoutResponse.from_model(payout) for payout in payouts],
    )


@router.post(
    "/payouts",
    response_model=ProviderPayoutRequestResponse,
    summary="Request provider payouts",
    description=(
        "Requests execution of ready provider payouts for the authenticated JWT account. "
        "An `Idempotency-Key` header is required."
    ),
    responses={
        200: {"description": "Provider payout request processed successfully."},
        403: {"description": "A non-JWT bearer token was supplied."},
        409: {
            "description": (
                "A payout request with the same idempotency key is already in progress or complete."
            )
        },
    },
)
async def request_provider_payouts(
    actor: CurrentJwtActor,
    fastapi_request: Request,
    session: SessionDep,
    idempotency_key: ValidatedIdempotencyKey,
) -> ProviderPayoutRequestResponse:
    service = PayoutExecutionService(
        session,
        payout_executor=_get_payout_executor(fastapi_request),
    )
    result = await service.request_provider_payouts(actor, idempotency_key=idempotency_key)
    return ProviderPayoutRequestResponse.from_values(
        idempotency_key=result.idempotency_key,
        requested_count=result.requested_count,
        sent_count=result.sent_count,
        failed_count=result.failed_count,
        payouts=[ProviderPayoutResponse.from_model(payout) for payout in result.payouts],
    )
