import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import create_account

from app.core.errors import NotFoundError
from app.services.accounts import get_account, update_display_name


@pytest.mark.asyncio
async def test_get_account_returns_persisted_account(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    account_id = await create_account(
        db_session_factory,
        wallet_address="0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6",
        display_name="Alpha",
    )

    async with db_session_factory() as session:
        account = await get_account(session=session, account_id=account_id)

    assert account.id == account_id
    assert account.display_name == "Alpha"
    assert account.wallet_address == "0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6"


@pytest.mark.asyncio
async def test_get_account_raises_not_found_for_missing_id(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await get_account(session=session, account_id=999_999)


@pytest.mark.asyncio
async def test_update_display_name_persists_and_advances_updated_at(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    account_id = await create_account(
        db_session_factory,
        display_name="Alpha",
    )

    async with db_session_factory() as session:
        original = await get_account(session=session, account_id=account_id)
        original_updated_at = original.updated_at

    async with db_session_factory() as session:
        await update_display_name(
            session=session,
            account_id=account_id,
            display_name="Bravo",
        )

    async with db_session_factory() as session:
        persisted = await get_account(session=session, account_id=account_id)

    assert persisted.display_name == "Bravo"
    assert persisted.updated_at > original_updated_at


@pytest.mark.asyncio
async def test_update_display_name_raises_not_found_for_missing_id(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError):
            await update_display_name(
                session=session,
                account_id=999_999,
                display_name="Bravo",
            )
