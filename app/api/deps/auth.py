from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.config import Settings, get_settings
from app.core.security import AuthTokenType, InvalidTokenError, decode_jwt, hash_api_key
from app.db.session import get_db_session
from app.repositories.account_repo import AccountRepository
from app.repositories.api_key_repo import ApiKeyRepository

AUTHORIZATION_HEADER = "Authorization"


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _extract_bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or not token:
        raise _unauthorized("Bearer token is required")
    return token


async def _build_actor_context(
    session: AsyncSession,
    *,
    authorization: str,
    settings: Settings,
) -> ActorContext:
    token = _extract_bearer_token(authorization)
    account_repo = AccountRepository(session)
    if token.startswith(settings.api_key_prefix):
        api_key_repo = ApiKeyRepository(session)
        api_key = await api_key_repo.get_by_hash(hash_api_key(token))
        if api_key is None or api_key.revoked_at is not None:
            raise _unauthorized("invalid api key")
        if api_key.expires_at is not None and api_key.expires_at <= datetime.now(UTC):
            raise _unauthorized("api key has expired")
        account = await account_repo.get(api_key.account_id)
        if account is None:
            raise _unauthorized("authenticated account does not exist")
        api_key_repo.touch_last_used(api_key)
        await session.commit()
        return ActorContext(
            account_id=account.id,
            is_admin=account.is_admin,
            account_type=getattr(account, "account_type", "human"),
            auth_method="api_key",
            wallet_address=getattr(account, "wallet_address", ""),
        )

    try:
        claims = decode_jwt(
            token,
            secret_key=settings.jwt_secret_key,
            expected_token_type=AuthTokenType.ACCESS,
        )
    except InvalidTokenError as exc:
        raise _unauthorized("invalid access token") from exc

    account = await account_repo.get(claims.account_id)
    if account is None:
        raise _unauthorized("authenticated account does not exist")

    token_version = getattr(account, "token_version", 1)
    if token_version != claims.token_version:
        raise _unauthorized("access token is no longer valid")

    return ActorContext(
        account_id=account.id,
        is_admin=account.is_admin,
        account_type=getattr(account, "account_type", "human"),
        auth_method="jwt",
        wallet_address=getattr(account, "wallet_address", claims.wallet_address),
    )


async def get_optional_current_actor(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> ActorContext | None:
    if authorization is None:
        return None

    return await _build_actor_context(
        session,
        authorization=authorization,
        settings=get_settings(),
    )


async def get_current_actor(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    authorization: Annotated[str | None, Header(alias=AUTHORIZATION_HEADER)] = None,
) -> ActorContext:
    if authorization is None:
        detail = f"{AUTHORIZATION_HEADER} header is required"
        raise _unauthorized(detail)

    return await _build_actor_context(
        session,
        authorization=authorization,
        settings=get_settings(),
    )


async def get_admin_actor(
    actor: Annotated[ActorContext, Depends(get_current_actor)],
) -> ActorContext:
    if not actor.is_admin:
        raise _forbidden("admin privileges required")
    return actor


CurrentActor = Annotated[ActorContext, Depends(get_current_actor)]
OptionalCurrentActor = Annotated[ActorContext | None, Depends(get_optional_current_actor)]
AdminActor = Annotated[ActorContext, Depends(get_admin_actor)]
