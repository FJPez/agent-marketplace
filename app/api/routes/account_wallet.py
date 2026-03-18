from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentJwtActor
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


@router.post(
    "",
    response_model=WalletChangeInitiateResponse,
    summary="Start a wallet rotation challenge",
    description=(
        "Creates a wallet-change challenge for the authenticated JWT account and "
        "returns the nonce that must be signed by the replacement wallet."
    ),
    responses={
        200: {"description": "Wallet change challenge issued successfully."},
        403: {"description": "A non-JWT bearer token was supplied."},
        409: {"description": "A wallet change cannot be started in the current state."},
        422: {"description": "The proposed wallet address was invalid."},
    },
)
async def initiate_wallet_change(
    request: WalletChangeInitiateRequest,
    actor: CurrentJwtActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WalletChangeInitiateResponse:
    service = WalletChangeService(session, settings=get_settings())
    try:
        challenge = await service.initiate_change(
            actor,
            wallet_address=normalize_wallet_address(request.wallet_address),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except WalletChangeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return WalletChangeInitiateResponse(nonce=challenge.nonce, expires_at=challenge.expires_at)


@router.post(
    "/confirm",
    response_model=WalletChangeConfirmResponse,
    summary="Confirm a wallet rotation",
    description=(
        "Verifies the signed wallet-change challenge, rotates the account wallet, and "
        "returns fresh JWT tokens bound to the new wallet."
    ),
    responses={
        200: {"description": "Wallet rotation completed successfully."},
        403: {"description": "A non-JWT bearer token was supplied."},
        409: {"description": "The wallet change challenge could not be completed."},
    },
)
async def confirm_wallet_change(
    request: WalletChangeConfirmRequest,
    actor: CurrentJwtActor,
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
