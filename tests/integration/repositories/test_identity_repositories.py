from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.account_repo import AccountRepository


@pytest.mark.asyncio
async def test_account_repository_persists_and_updates_identity_fields(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        repo = AccountRepository(session)
        account = await repo.create(
            wallet_address="0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6",
            display_name="Alpha",
        )

    async with db_session_factory.begin() as session:
        repo = AccountRepository(session)
        account = await repo.get_by_wallet_address(
            "0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6",
        )

        assert account is not None
        assert account.display_name == "Alpha"
        assert account.token_version == 1

        repo.update_nonce(
            account,
            nonce="nonce-2",
            issued_at=datetime(2026, 3, 16, 12, 5, tzinfo=UTC),
        )
        repo.bump_token_version(account)

    async with db_session_factory.begin() as session:
        repo = AccountRepository(session)
        updated_account = await repo.get(account.id)

    assert updated_account is not None
    assert updated_account.nonce == "nonce-2"
    assert updated_account.nonce_issued_at == datetime(2026, 3, 16, 12, 5, tzinfo=UTC)
    assert updated_account.token_version == 2


@pytest.mark.asyncio
async def test_account_repository_updates_wallet_and_change_timestamp(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        repo = AccountRepository(session)
        account = await repo.create(
            wallet_address="0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6",
            display_name="Wallet Owner",
        )

    changed_at = datetime(2026, 3, 17, 9, 0, tzinfo=UTC)

    async with db_session_factory.begin() as session:
        repo = AccountRepository(session)
        account = await repo.get(account.id)
        assert account is not None
        repo.update_wallet(
            account,
            wallet_address="0x000000000000000000000000000000000000dEaD",
            wallet_changed_at=changed_at,
        )

    async with db_session_factory.begin() as session:
        repo = AccountRepository(session)
        updated_account = await repo.get_by_wallet_address(
            "0x000000000000000000000000000000000000dEaD",
        )

    assert updated_account is not None
    assert updated_account.wallet_changed_at == changed_at
