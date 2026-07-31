from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, account_id: int) -> Account | None:
        return await self._session.get(Account, account_id)
