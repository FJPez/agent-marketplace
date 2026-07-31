from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidInputError, NotFoundError
from app.core.security import generate_api_key
from app.db.models import ApiKey


async def create_api_key(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
    name: str | None,
    expires_at: datetime | None,
) -> tuple[ApiKey, str]:
    normalized_name = _normalize_name(name)
    _validate_expiry(expires_at)
    material = generate_api_key(settings.api_key_prefix)
    api_key = ApiKey(
        account_id=account_id,
        name=normalized_name,
        key_prefix=material.key_prefix,
        key_hash=material.key_hash,
        expires_at=expires_at,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return api_key, material.plaintext


async def list_api_keys(*, session: AsyncSession, account_id: int) -> list[ApiKey]:
    result = await session.scalars(
        select(ApiKey)
        .where(ApiKey.account_id == account_id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc()),
    )
    return list(result)


async def revoke_api_key(*, session: AsyncSession, account_id: int, api_key_id: int) -> None:
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is None or api_key.account_id != account_id:
        raise NotFoundError("api key not found")
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        await session.commit()


def _normalize_name(name: str | None) -> str | None:
    if name is None:
        return None
    normalized_name = name.strip()
    if not normalized_name:
        raise InvalidInputError("name must not be blank")
    if len(normalized_name) > 255:
        raise InvalidInputError("name must be at most 255 characters")
    return normalized_name


def _validate_expiry(expires_at: datetime | None) -> None:
    if expires_at is None:
        return
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise InvalidInputError("expires_at must include timezone information")
    if expires_at <= datetime.now(UTC):
        raise InvalidInputError("expires_at must be in the future")
