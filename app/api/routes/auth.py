from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentJwtActor
from app.core.config import get_settings
from app.core.security import normalize_wallet_address
from app.db.session import get_db_session
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
from app.services.api_key_service import ApiKeyService
from app.services.auth_service import AuthService

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
    address: Annotated[str, Query(min_length=42, max_length=42)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthNonceResponse:
    service = AuthService(session, settings=get_settings())
    try:
        nonce = await service.issue_nonce(wallet_address=normalize_wallet_address(address))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
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
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthVerifyResponse:
    service = AuthService(session, settings=get_settings())
    result = await service.verify_wallet(
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
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthRefreshResponse:
    service = AuthService(session, settings=get_settings())
    access_token = await service.refresh_access_token(refresh_token=request.refresh_token)
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
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiKeyCreateResponse:
    service = ApiKeyService(session, settings=get_settings())
    api_key, plaintext = await service.create_key(
        actor,
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
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ApiKeyResponse]:
    service = ApiKeyService(session, settings=get_settings())
    api_keys = await service.list_keys(actor)
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
        for api_key in api_keys
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
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    service = ApiKeyService(session, settings=get_settings())
    await service.revoke_key(actor, api_key_id=api_key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
