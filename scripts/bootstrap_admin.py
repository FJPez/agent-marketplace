from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import normalize_database_url
from app.core.security import normalize_wallet_address
from app.db.models import Account

ADMIN_WALLET_ENV_VAR = "APP_BOOTSTRAP_ADMIN_WALLET"
ADMIN_DISPLAY_NAME = "Admin"


class BootstrapAdminError(RuntimeError):
    pass


def _get_required_env_var(env_name: str) -> str:
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    raise BootstrapAdminError(f"{env_name} is required")


def _normalize_admin_wallet(admin_wallet: str) -> str:
    try:
        return normalize_wallet_address(admin_wallet)
    except ValueError as exc:
        msg = f"{ADMIN_WALLET_ENV_VAR} is not a valid wallet address"
        raise BootstrapAdminError(msg) from exc


async def bootstrap_admin(*, database_url: str, admin_wallet: str) -> str:
    wallet_address = _normalize_admin_wallet(admin_wallet)
    engine = create_async_engine(normalize_database_url(database_url), pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with session_factory.begin() as session:
            account = await session.scalar(
                select(Account).where(Account.wallet_address == wallet_address).with_for_update(),
            )
            if account is None:
                session.add(
                    Account(
                        wallet_address=wallet_address,
                        display_name=ADMIN_DISPLAY_NAME,
                        account_type="human",
                        is_admin=True,
                    )
                )
            else:
                account.is_admin = True
            return wallet_address
    finally:
        await engine.dispose()


async def _async_main() -> int:
    wallet_address = await bootstrap_admin(
        database_url=_get_required_env_var("APP_DATABASE_URL"),
        admin_wallet=_get_required_env_var(ADMIN_WALLET_ENV_VAR),
    )
    sys.stdout.write(f"{wallet_address}\n")
    return 0


def main() -> int:
    try:
        return asyncio.run(_async_main())
    except BootstrapAdminError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
