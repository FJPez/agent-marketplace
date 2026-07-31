from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        account_id: int,
        name: str | None,
        key_prefix: str,
        key_hash: str,
        expires_at: datetime | None,
    ) -> ApiKey:
        api_key = ApiKey(
            account_id=account_id,
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            expires_at=expires_at,
        )
        self._session.add(api_key)
        return api_key

    async def get(self, api_key_id: int) -> ApiKey | None:
        return await self._session.get(ApiKey, api_key_id)

    async def list_for_account(self, *, account_id: int) -> list[ApiKey]:
        statement: Select[tuple[ApiKey]] = (
            select(ApiKey)
            .where(ApiKey.account_id == account_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        )
        return list(await self._session.scalars(statement))

    def revoke(self, api_key: ApiKey) -> ApiKey:
        api_key.revoked_at = datetime.now(UTC)
        return api_key
