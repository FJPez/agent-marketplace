import pytest
from scripts.bootstrap_admin import BootstrapAdminError, bootstrap_admin, main
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account

ADMIN_WALLET = "0x89AEF553A06ab0C3173e79DE1Ce241A9ed3b992C"
OTHER_ADMIN_WALLET = "0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6"


def _database_url(db_session_factory: async_sessionmaker[AsyncSession]) -> str:
    bind = db_session_factory.kw["bind"]
    return bind.url.render_as_string(hide_password=False)


async def _find_account(
    db_session_factory: async_sessionmaker[AsyncSession],
    wallet_address: str,
) -> Account | None:
    async with db_session_factory() as session:
        return await session.scalar(
            select(Account).where(Account.wallet_address == wallet_address),
        )


async def test_bootstrap_admin_creates_admin_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    wallet_address = await bootstrap_admin(
        database_url=_database_url(db_session_factory),
        admin_wallet=ADMIN_WALLET,
    )

    account = await _find_account(db_session_factory, ADMIN_WALLET)
    assert wallet_address == ADMIN_WALLET
    assert account is not None
    assert account.display_name == "Admin"
    assert account.account_type == "human"
    assert account.is_admin is True


async def test_bootstrap_admin_normalizes_the_wallet_to_its_checksum_address(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            Account(
                wallet_address=ADMIN_WALLET,
                display_name="Signed In User",
                account_type="human",
                is_admin=False,
            )
        )

    wallet_address = await bootstrap_admin(
        database_url=_database_url(db_session_factory),
        admin_wallet=ADMIN_WALLET.lower(),
    )

    async with db_session_factory() as session:
        accounts = list(await session.scalars(select(Account)))
    assert wallet_address == ADMIN_WALLET
    assert len(accounts) == 1
    assert accounts[0].is_admin is True
    assert accounts[0].display_name == "Signed In User"


async def test_bootstrap_admin_promotes_existing_account_without_touching_others(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory.begin() as session:
        session.add_all(
            [
                Account(
                    wallet_address=ADMIN_WALLET,
                    display_name="Existing User",
                    account_type="human",
                    is_admin=False,
                ),
                Account(
                    wallet_address=OTHER_ADMIN_WALLET,
                    display_name="Existing Admin",
                    account_type="human",
                    is_admin=True,
                ),
            ]
        )

    await bootstrap_admin(
        database_url=_database_url(db_session_factory),
        admin_wallet=ADMIN_WALLET,
    )

    promoted = await _find_account(db_session_factory, ADMIN_WALLET)
    other = await _find_account(db_session_factory, OTHER_ADMIN_WALLET)
    assert promoted is not None
    assert promoted.is_admin is True
    assert promoted.display_name == "Existing User"
    assert other is not None
    assert other.is_admin is True


async def test_bootstrap_admin_is_idempotent(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await bootstrap_admin(
        database_url=_database_url(db_session_factory),
        admin_wallet=ADMIN_WALLET,
    )
    second = await bootstrap_admin(
        database_url=_database_url(db_session_factory),
        admin_wallet=ADMIN_WALLET,
    )

    async with db_session_factory() as session:
        accounts = list(
            await session.scalars(select(Account).where(Account.wallet_address == first)),
        )
    assert first == second
    assert len(accounts) == 1
    assert accounts[0].is_admin is True


async def test_bootstrap_admin_accepts_plain_postgres_database_url(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    database_url = _database_url(db_session_factory).replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    await bootstrap_admin(database_url=database_url, admin_wallet=ADMIN_WALLET)

    account = await _find_account(db_session_factory, ADMIN_WALLET)
    assert account is not None
    assert account.is_admin is True


async def test_bootstrap_admin_rejects_invalid_wallet_address() -> None:
    with pytest.raises(
        BootstrapAdminError,
        match="APP_BOOTSTRAP_ADMIN_WALLET is not a valid wallet address",
    ):
        await bootstrap_admin(
            database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace",
            admin_wallet="not-a-wallet",
        )


def test_main_reports_missing_admin_wallet(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+asyncpg://unused:5432/unused")
    monkeypatch.delenv("APP_BOOTSTRAP_ADMIN_WALLET", raising=False)

    exit_code = main()

    assert exit_code == 1
    assert capsys.readouterr().err == "APP_BOOTSTRAP_ADMIN_WALLET is required\n"
