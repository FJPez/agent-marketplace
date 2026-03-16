from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
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
from app.services.api_key_service import ApiKeyNotFoundError, ApiKeyService, ApiKeyValidationError
from app.services.auth_service import AuthenticationError, AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/nonce", response_model=AuthNonceResponse)
async def get_auth_nonce(
    address: Annotated[str, Query(min_length=42, max_length=42)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthNonceResponse:
    service = AuthService(session, settings=get_settings())
    try:
        nonce = await service.issue_nonce(wallet_address=normalize_wallet_address(address))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return AuthNonceResponse(nonce=nonce)


@router.post("/verify", response_model=AuthVerifyResponse)
async def verify_auth(
    request: AuthVerifyRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthVerifyResponse:
    service = AuthService(session, settings=get_settings())
    try:
        result = await service.verify_wallet(
            message=request.message,
            signature=request.signature,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return AuthVerifyResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        account=AccountResponse.model_validate(result.account),
    )


@router.post("/refresh", response_model=AuthRefreshResponse)
async def refresh_auth(
    request: AuthRefreshRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthRefreshResponse:
    service = AuthService(session, settings=get_settings())
    try:
        access_token = await service.refresh_access_token(refresh_token=request.refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return AuthRefreshResponse(access_token=access_token)


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    request: ApiKeyCreateRequest,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiKeyCreateResponse:
    service = ApiKeyService(session, settings=get_settings())
    try:
        api_key, plaintext = await service.create_key(
            actor,
            name=request.name,
            expires_at=request.expires_at,
        )
    except ApiKeyValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
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


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    actor: CurrentActor,
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


@router.delete("/api-keys/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    api_key_id: int,
    actor: CurrentActor,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    service = ApiKeyService(session, settings=get_settings())
    try:
        await service.revoke_key(actor, api_key_id=api_key_id)
    except ApiKeyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="api key not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
