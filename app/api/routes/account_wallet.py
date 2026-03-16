from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.core.config import get_settings
from app.core.security import normalize_wallet_address
from app.db.session import get_db_session
from app.schemas.account import (
    AccountResponse,
    WalletChangeConfirmRequest,
    WalletChangeConfirmResponse,
    WalletChangeInitiateRequest,
    WalletChangeInitiateResponse,
)
from app.services.wallet_change_service import WalletChangeError, WalletChangeService

router = APIRouter(prefix="/account/wallet", tags=["account"])


@router.post("", response_model=WalletChangeInitiateResponse)
async def initiate_wallet_change(
    request: WalletChangeInitiateRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WalletChangeInitiateResponse:
    service = WalletChangeService(session, settings=get_settings())
    try:
        challenge = await service.initiate_change(
            actor,
            wallet_address=normalize_wallet_address(request.wallet_address),
        )
    except (ValueError, WalletChangeError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return WalletChangeInitiateResponse(nonce=challenge.nonce, expires_at=challenge.expires_at)


@router.post("/confirm", response_model=WalletChangeConfirmResponse)
async def confirm_wallet_change(
    request: WalletChangeConfirmRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WalletChangeConfirmResponse:
    service = WalletChangeService(session, settings=get_settings())
    try:
        account, tokens = await service.confirm_change(
            actor,
            message=request.message,
            signature=request.signature,
        )
    except WalletChangeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return WalletChangeConfirmResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        account=AccountResponse.model_validate(account),
    )
