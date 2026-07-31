from fastapi import APIRouter, Response, status

from app.api.deps.auth import CurrentJwtActor
from app.api.deps.database import SessionDep
from app.api.deps.settings import SettingsDep
from app.schemas.account import AccountResponse
from app.schemas.auth import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    AuthNonceResponse,
    AuthRefreshRequest,
    AuthRefreshResponse,
    AuthVerifyRequest,
    AuthVerifyResponse,
)
from app.schemas.common import WalletAddress
from app.services import api_keys, auth

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get(
    "/nonce",
    response_model=AuthNonceResponse,
    summary="Issue a wallet-auth nonce",
    description=(
        "Returns a single-use nonce for the supplied wallet address. The nonce is then "
        "embedded into the SIWE-style message signed before calling `/v1/auth/verify`."
    ),
    responses={
        200: {"description": "Nonce issued successfully."},
        422: {"description": "The supplied wallet address was invalid."},
    },
)
async def get_auth_nonce(
    address: WalletAddress,
    session: SessionDep,
    settings: SettingsDep,
) -> AuthNonceResponse:
    nonce = await auth.issue_nonce(
        session=session,
        settings=settings,
        wallet_address=address,
    )
    return AuthNonceResponse(nonce=nonce)


@router.post(
    "/verify",
    response_model=AuthVerifyResponse,
    summary="Verify a signed wallet-auth message",
    description=(
        "Exchanges a signed SIWE-style message for marketplace JWT credentials. "
        "Successful responses include both access and refresh tokens."
    ),
    responses={
        200: {"description": "Wallet authenticated successfully."},
        401: {"description": "The signed message or signature could not be verified."},
    },
)
async def verify_auth(
    request: AuthVerifyRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> AuthVerifyResponse:
    result = await auth.verify_wallet(
        session=session,
        settings=settings,
        message=request.message,
        signature=request.signature,
    )
    return AuthVerifyResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        account=AccountResponse.model_validate(result.account),
    )


@router.post(
    "/refresh",
    response_model=AuthRefreshResponse,
    summary="Refresh an access token",
    description="Returns a fresh access token for a valid refresh token.",
    responses={
        200: {"description": "Access token refreshed successfully."},
        401: {"description": "The supplied refresh token was invalid or expired."},
    },
)
async def refresh_auth(
    request: AuthRefreshRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> AuthRefreshResponse:
    access_token = await auth.refresh_access_token(
        session=session,
        settings=settings,
        refresh_token=request.refresh_token,
    )
    return AuthRefreshResponse(access_token=access_token)


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
    description=(
        "Creates a new API key for the authenticated JWT account. The plaintext key is "
        "returned exactly once in the response."
    ),
    responses={
        201: {"description": "API key created successfully."},
        403: {"description": "A non-JWT bearer token was supplied."},
        422: {"description": "The API key request was invalid."},
    },
)
async def create_api_key(
    request: ApiKeyCreateRequest,
    actor: CurrentJwtActor,
    session: SessionDep,
    settings: SettingsDep,
) -> ApiKeyCreateResponse:
    api_key, plaintext = await api_keys.create_api_key(
        session=session,
        settings=settings,
        account_id=actor.account_id,
        name=request.name,
        expires_at=request.expires_at,
    )
    return ApiKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        revoked_at=api_key.revoked_at,
        created_at=api_key.created_at,
        api_key=plaintext,
    )


@router.get(
    "/api-keys",
    response_model=list[ApiKeyResponse],
    summary="List API keys",
    description=(
        "Lists API key metadata for the authenticated JWT account. Plaintext key "
        "material is never returned by this route."
    ),
    responses={
        200: {"description": "API key metadata returned successfully."},
        403: {"description": "A non-JWT bearer token was supplied."},
    },
)
async def list_api_keys(
    actor: CurrentJwtActor,
    session: SessionDep,
) -> list[ApiKeyResponse]:
    account_api_keys = await api_keys.list_api_keys(session=session, account_id=actor.account_id)
    return [
        ApiKeyResponse(
            id=api_key.id,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            expires_at=api_key.expires_at,
            last_used_at=api_key.last_used_at,
            revoked_at=api_key.revoked_at,
            created_at=api_key.created_at,
        )
        for api_key in account_api_keys
    ]


@router.delete(
    "/api-keys/{api_key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
    description="Revokes an API key owned by the authenticated JWT account.",
    responses={
        204: {"description": "API key revoked successfully."},
        403: {"description": "A non-JWT bearer token was supplied."},
        404: {"description": "The requested API key does not exist."},
    },
)
async def revoke_api_key(
    api_key_id: int,
    actor: CurrentJwtActor,
    session: SessionDep,
) -> Response:
    await api_keys.revoke_api_key(
        session=session,
        account_id=actor.account_id,
        api_key_id=api_key_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
