from __future__ import annotations

from secrets import token_hex
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.core.security import AuthTokenType, create_jwt
from app.db.models import Account
from app.services.api_keys import create_api_key

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def wallet_address_for_index(index: int) -> str:
    return f"0x{index:040x}"


async def create_account(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    wallet_address: str | None = None,
    display_name: str = "Anonymous",
    account_type: str = "human",
    is_admin: bool = False,
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(
            wallet_address=wallet_address or f"0x{token_hex(20)}",
            display_name=display_name,
            account_type=account_type,
            is_admin=is_admin,
        )
        session.add(account)
        await session.flush()
        return account.id


async def auth_headers_for_account(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
) -> dict[str, str]:
    async with db_session_factory() as session:
        account = await session.get(Account, account_id)

    assert account is not None
    assert account.wallet_address is not None
    token = create_jwt(
        secret_key=get_settings().jwt_secret_key.get_secret_value(),
        account_id=account.id,
        wallet_address=account.wallet_address,
        token_version=account.token_version,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=get_settings().jwt_access_token_expiry,
    )
    return {"Authorization": f"Bearer {token}"}


async def api_key_headers_for_account(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
) -> dict[str, str]:
    async with db_session_factory() as session:
        _, plaintext = await create_api_key(
            session=session,
            settings=get_settings(),
            account_id=account_id,
            name=None,
            expires_at=None,
        )
    return {"Authorization": f"Bearer {plaintext}"}


def auth_headers_for_account_id(
    account_id: int,
    *,
    token_version: int = 1,
) -> dict[str, str]:
    settings = get_settings()
    token = create_jwt(
        secret_key=settings.jwt_secret_key.get_secret_value(),
        account_id=account_id,
        wallet_address=wallet_address_for_index(account_id),
        token_version=token_version,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=settings.jwt_access_token_expiry,
    )
    return {"Authorization": f"Bearer {token}"}
