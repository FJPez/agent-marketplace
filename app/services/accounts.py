from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models import Account


async def get_account(*, session: AsyncSession, account_id: int) -> Account:
    account = await session.get(Account, account_id)
    if account is None:
        raise NotFoundError("account not found")
    return account


async def update_display_name(
    *,
    session: AsyncSession,
    account_id: int,
    display_name: str,
) -> Account:
    account = await get_account(session=session, account_id=account_id)
    account.display_name = display_name
    account.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(account)
    return account
