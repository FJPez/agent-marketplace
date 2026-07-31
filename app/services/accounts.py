from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidInputError, NotFoundError
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
    normalized_display_name = display_name.strip()
    if not normalized_display_name:
        raise InvalidInputError("display_name must not be blank")
    if len(normalized_display_name) > 255:
        raise InvalidInputError("display_name must be at most 255 characters")
    account = await get_account(session=session, account_id=account_id)
    account.display_name = normalized_display_name
    account.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(account)
    return account
