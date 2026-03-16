from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.actor import ActorContext
from app.core.security import AuthTokenType, InvalidTokenError, decode_jwt, hash_api_key
from app.repositories.account_repo import AccountRepository
from app.repositories.api_key_repo import ApiKeyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings


class AuthResolutionError(Exception):
    pass


class JwtAuthRequiredError(Exception):
    pass


class AuthResolutionService:
    def __init__(self, session: AsyncSession, *, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._account_repo = AccountRepository(session)
        self._api_key_repo = ApiKeyRepository(session)

    async def resolve_actor(
        self,
        *,
        authorization: str,
        touch_api_key: bool = True,
    ) -> ActorContext:
        token = self._extract_bearer_token(authorization)
        if token.startswith(self._settings.api_key_prefix):
            return await self._resolve_api_key_actor(token, touch_api_key=touch_api_key)
        return await self._resolve_jwt_actor(token)

    async def resolve_jwt_actor(self, *, authorization: str) -> ActorContext:
        actor = await self.resolve_actor(authorization=authorization)
        if actor.auth_method != "jwt":
            raise JwtAuthRequiredError("jwt authentication required")
        return actor

    def _extract_bearer_token(self, authorization: str) -> str:
        scheme, _, token = authorization.partition(" ")
        if scheme != "Bearer" or not token:
            msg = "Bearer token is required"
            raise AuthResolutionError(msg)
        return token

    async def _resolve_api_key_actor(
        self,
        token: str,
        *,
        touch_api_key: bool,
    ) -> ActorContext:
        api_key = await self._api_key_repo.get_by_hash(hash_api_key(token))
        if api_key is None or api_key.revoked_at is not None:
            msg = "invalid api key"
            raise AuthResolutionError(msg)
        if api_key.expires_at is not None and api_key.expires_at <= datetime.now(UTC):
            msg = "api key has expired"
            raise AuthResolutionError(msg)

        account = await self._account_repo.get(api_key.account_id)
        if account is None:
            msg = "authenticated account does not exist"
            raise AuthResolutionError(msg)

        if touch_api_key:
            self._api_key_repo.touch_last_used(api_key)
            await self._session.commit()
        return ActorContext(
            account_id=account.id,
            is_admin=account.is_admin,
            account_type=getattr(account, "account_type", "human"),
            auth_method="api_key",
            wallet_address=getattr(account, "wallet_address", "") or "",
        )

    async def _resolve_jwt_actor(self, token: str) -> ActorContext:
        try:
            claims = decode_jwt(
                token,
                secret_key=self._settings.jwt_secret_key,
                expected_token_type=AuthTokenType.ACCESS,
            )
        except InvalidTokenError as exc:
            msg = "invalid access token"
            raise AuthResolutionError(msg) from exc

        account = await self._account_repo.get(claims.account_id)
        if account is None:
            msg = "authenticated account does not exist"
            raise AuthResolutionError(msg)

        token_version = getattr(account, "token_version", 1)
        if token_version != claims.token_version:
            msg = "access token is no longer valid"
            raise AuthResolutionError(msg)

        return ActorContext(
            account_id=account.id,
            is_admin=account.is_admin,
            account_type=getattr(account, "account_type", "human"),
            auth_method="jwt",
            wallet_address=getattr(account, "wallet_address", claims.wallet_address),
        )
