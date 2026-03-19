from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.security import generate_api_key
from app.db.models import ApiKey
from app.repositories.api_key_repo import ApiKeyRepository


class ApiKeyNotFoundError(Exception):
    pass


class ApiKeyValidationError(Exception):
    pass


class ApiKeyService:
    def __init__(self, session: AsyncSession, *, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._api_key_repo = ApiKeyRepository(session)

    async def create_key(
        self,
        actor: ActorContext,
        *,
        name: str | None,
        expires_at: datetime | None,
    ) -> tuple[ApiKey, str]:
        normalized_name = name.strip() if name is not None else None
        if normalized_name is not None and not normalized_name:
            raise ApiKeyValidationError("name must not be blank")
        if expires_at is not None and (expires_at.tzinfo is None or expires_at.utcoffset() is None):
            raise ApiKeyValidationError("expires_at must include timezone information")
        if expires_at is not None and expires_at <= datetime.now(UTC):
            raise ApiKeyValidationError("expires_at must be in the future")

        material = generate_api_key(self._settings.api_key_prefix)
        api_key = self._api_key_repo.add(
            account_id=actor.account_id,
            name=normalized_name,
            key_prefix=material.key_prefix,
            key_hash=material.key_hash,
            expires_at=expires_at,
        )
        await self._session.commit()
        await self._session.refresh(api_key)
        return api_key, material.plaintext

    async def list_keys(self, actor: ActorContext) -> list[ApiKey]:
        return await self._api_key_repo.list_for_account(account_id=actor.account_id)

    async def revoke_key(self, actor: ActorContext, *, api_key_id: int) -> None:
        api_key = await self._api_key_repo.get(api_key_id)
        if api_key is None or api_key.account_id != actor.account_id:
            raise ApiKeyNotFoundError
        if api_key.revoked_at is None:
            self._api_key_repo.revoke(api_key)
            await self._session.commit()
