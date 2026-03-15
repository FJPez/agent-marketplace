from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self) -> Account:
        account = Account()
        self._session.add(account)
        await self._session.flush()
        return account

    async def exists(self, account_id: int) -> bool:
        statement = select(Account.id).where(Account.id == account_id).limit(1)
        result = await self._session.scalar(statement)
        return result is not None

    async def get(self, account_id: int) -> Account | None:
        return await self._session.get(Account, account_id)
