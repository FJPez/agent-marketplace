from fastapi import APIRouter

from app.api.deps.auth import CurrentJwtActor
from app.api.deps.database import SessionDep
from app.api.deps.settings import SettingsDep
from app.schemas.account import (
    AccountResponse,
    WalletChangeConfirmRequest,
    WalletChangeConfirmResponse,
    WalletChangeInitiateRequest,
    WalletChangeInitiateResponse,
)
from app.services import wallet_changes

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
    session: SessionDep,
    settings: SettingsDep,
) -> WalletChangeInitiateResponse:
    challenge = await wallet_changes.initiate_wallet_change(
        session=session,
        settings=settings,
        account_id=actor.account_id,
        wallet_address=request.wallet_address,
    )
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
    session: SessionDep,
    settings: SettingsDep,
) -> WalletChangeConfirmResponse:
    account, tokens = await wallet_changes.confirm_wallet_change(
        session=session,
        settings=settings,
        account_id=actor.account_id,
        message=request.message,
        signature=request.signature,
    )
    return WalletChangeConfirmResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        account=AccountResponse.model_validate(account),
    )
