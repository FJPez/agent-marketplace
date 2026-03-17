import pytest
from scripts.bootstrap_admin import BootstrapAdminError, bootstrap_admin
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account


def _database_url(db_session_factory: async_sessionmaker[AsyncSession]) -> str:
    bind = db_session_factory.kw["bind"]
    return bind.url.render_as_string(hide_password=False)


@pytest.mark.asyncio
async def test_bootstrap_admin_creates_treasury_admin_account(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    treasury_wallet = await bootstrap_admin(
        database_url=_database_url(db_session_factory),
        treasury_private_key="0x" + "cd" * 32,
    )

    async with db_session_factory() as session:
        account = await session.scalar(
            select(Account).where(Account.wallet_address == treasury_wallet),
        )

    assert account is not None
    assert account.wallet_address == treasury_wallet
    assert account.display_name == "Treasury Admin"
    assert account.account_type == "human"
    assert account.is_admin is True


@pytest.mark.asyncio
async def test_bootstrap_admin_promotes_existing_treasury_account_without_touching_others(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    treasury_wallet = "0x89AEF553A06ab0C3173e79DE1Ce241A9ed3b992C"

    async with db_session_factory.begin() as session:
        treasury_account = Account(
            wallet_address=treasury_wallet,
            display_name="Treasury User",
            account_type="human",
            is_admin=False,
        )
        unrelated_admin = Account(
            wallet_address="0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6",
            display_name="Existing Admin",
            account_type="human",
            is_admin=True,
        )
        session.add_all([treasury_account, unrelated_admin])

    returned_wallet = await bootstrap_admin(
        database_url=_database_url(db_session_factory),
        treasury_private_key="0x" + "cd" * 32,
    )

    async with db_session_factory() as session:
        treasury_account = await session.scalar(
            select(Account).where(Account.wallet_address == treasury_wallet),
        )
        unrelated_admin = await session.scalar(
            select(Account).where(
                Account.wallet_address == "0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6"
            ),
        )

    assert returned_wallet == treasury_wallet
    assert treasury_account is not None
    assert treasury_account.is_admin is True
    assert treasury_account.display_name == "Treasury User"
    assert unrelated_admin is not None
    assert unrelated_admin.is_admin is True


@pytest.mark.asyncio
async def test_bootstrap_admin_is_idempotent(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    first_wallet = await bootstrap_admin(
        database_url=_database_url(db_session_factory),
        treasury_private_key="0x" + "cd" * 32,
    )
    second_wallet = await bootstrap_admin(
        database_url=_database_url(db_session_factory),
        treasury_private_key="0x" + "cd" * 32,
    )

    async with db_session_factory() as session:
        accounts = list(
            await session.scalars(select(Account).where(Account.wallet_address == first_wallet)),
        )

    assert first_wallet == second_wallet
    assert len(accounts) == 1
    assert accounts[0].is_admin is True


@pytest.mark.asyncio
async def test_bootstrap_admin_accepts_plain_postgres_database_url(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    database_url = _database_url(db_session_factory).replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    treasury_wallet = await bootstrap_admin(
        database_url=database_url,
        treasury_private_key="0x" + "cd" * 32,
    )

    async with db_session_factory() as session:
        account = await session.scalar(
            select(Account).where(Account.wallet_address == treasury_wallet),
        )

    assert account is not None
    assert account.is_admin is True


@pytest.mark.asyncio
async def test_bootstrap_admin_rejects_invalid_treasury_private_key() -> None:
    with pytest.raises(BootstrapAdminError, match="APP_TREASURY_PRIVATE_KEY is not a valid"):
        await bootstrap_admin(
            database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace",
            treasury_private_key="not-a-key",
        )
