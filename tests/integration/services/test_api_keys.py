from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import create_account

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.core.security import hash_api_key
from app.db.models import ApiKey
from app.services.api_keys import create_api_key, list_api_keys, revoke_api_key

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]


def _settings() -> Settings:
    return Settings(api_key_prefix="amp_")


async def test_create_api_key_persists_row_and_returns_plaintext(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings()
    account_id = await create_account(db_session_factory)
    expires_at = datetime.now(UTC) + timedelta(days=30)

    async with db_session_factory() as session:
        api_key, plaintext = await create_api_key(
            session=session,
            settings=settings,
            account_id=account_id,
            name="worker-key",
            expires_at=expires_at,
        )

    assert plaintext.startswith(settings.api_key_prefix)
    assert api_key.created_at is not None

    async with db_session_factory() as session:
        persisted = await session.get(ApiKey, api_key.id)

    assert persisted is not None
    assert persisted.key_hash == hash_api_key(plaintext)
    assert persisted.key_prefix == api_key.key_prefix
    assert persisted.name == "worker-key"
    assert persisted.expires_at == expires_at


async def test_list_api_keys_returns_only_requesting_account_ordered_desc(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings()
    account_id = await create_account(db_session_factory)
    other_account_id = await create_account(db_session_factory)

    async with db_session_factory() as session:
        await create_api_key(
            session=session,
            settings=settings,
            account_id=other_account_id,
            name="other-account-key",
            expires_at=None,
        )

    created_ids: list[int] = []
    for index in range(3):
        async with db_session_factory() as session:
            api_key, _ = await create_api_key(
                session=session,
                settings=settings,
                account_id=account_id,
                name=f"key-{index}",
                expires_at=None,
            )
            created_ids.append(api_key.id)

    async with db_session_factory() as session:
        keys = await list_api_keys(session=session, account_id=account_id)

    assert [key.id for key in keys] == sorted(created_ids, reverse=True)
    assert all(key.account_id == account_id for key in keys)


async def test_revoke_api_key_sets_revoked_at_and_is_idempotent(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings()
    account_id = await create_account(db_session_factory)

    async with db_session_factory() as session:
        api_key, _ = await create_api_key(
            session=session,
            settings=settings,
            account_id=account_id,
            name="worker-key",
            expires_at=None,
        )

    async with db_session_factory() as session:
        await revoke_api_key(session=session, account_id=account_id, api_key_id=api_key.id)

    async with db_session_factory() as session:
        persisted = await session.get(ApiKey, api_key.id)

    assert persisted is not None
    assert persisted.revoked_at is not None
    first_revoked_at = persisted.revoked_at

    async with db_session_factory() as session:
        await revoke_api_key(session=session, account_id=account_id, api_key_id=api_key.id)

    async with db_session_factory() as session:
        persisted_again = await session.get(ApiKey, api_key.id)

    assert persisted_again is not None
    assert persisted_again.revoked_at == first_revoked_at


async def test_revoke_api_key_raises_not_found_for_missing_id(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_account(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await revoke_api_key(session=session, account_id=account_id, api_key_id=999_999)


async def test_revoke_api_key_raises_not_found_for_other_accounts_key(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings()
    account_id = await create_account(db_session_factory)
    other_account_id = await create_account(db_session_factory)

    async with db_session_factory() as session:
        api_key, _ = await create_api_key(
            session=session,
            settings=settings,
            account_id=other_account_id,
            name="other-account-key",
            expires_at=None,
        )

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await revoke_api_key(session=session, account_id=account_id, api_key_id=api_key.id)
