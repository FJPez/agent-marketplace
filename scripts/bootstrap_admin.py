from __future__ import annotations

import asyncio
import os
import sys

from eth_account import Account as EthAccount
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Account

TREASURY_ADMIN_DISPLAY_NAME = "Treasury Admin"


class BootstrapAdminError(RuntimeError):
    pass


def _get_required_env_var(env_name: str) -> str:
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    raise BootstrapAdminError(f"{env_name} is required")


def _wallet_address_from_private_key(private_key: str) -> str:
    try:
        return EthAccount.from_key(private_key).address
    except (TypeError, ValueError) as exc:
        msg = "APP_TREASURY_PRIVATE_KEY is not a valid EVM private key"
        raise BootstrapAdminError(msg) from exc


async def bootstrap_admin(*, database_url: str, treasury_private_key: str) -> str:
    treasury_wallet = _wallet_address_from_private_key(treasury_private_key)
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with session_factory.begin() as session:
            account = await session.scalar(
                select(Account).where(Account.wallet_address == treasury_wallet).with_for_update(),
            )
            if account is None:
                account = Account(
                    wallet_address=treasury_wallet,
                    display_name=TREASURY_ADMIN_DISPLAY_NAME,
                    account_type="human",
                    is_admin=True,
                )
                session.add(account)
                await session.flush()
                return treasury_wallet

            account.is_admin = True
            await session.flush()
            return treasury_wallet
    finally:
        await engine.dispose()


async def _async_main() -> int:
    treasury_wallet = await bootstrap_admin(
        database_url=_get_required_env_var("APP_DATABASE_URL"),
        treasury_private_key=_get_required_env_var("APP_TREASURY_PRIVATE_KEY"),
    )
    sys.stdout.write(f"{treasury_wallet}\n")
    return 0


def main() -> int:
    try:
        return asyncio.run(_async_main())
    except BootstrapAdminError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
