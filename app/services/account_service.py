from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.db.models import Account
from app.repositories.account_repo import AccountRepository


class AccountValidationError(Exception):
    pass


class AccountNotFoundError(Exception):
    pass


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._account_repo = AccountRepository(session)

    async def get_current_account(self, actor: ActorContext) -> Account:
        account = await self._account_repo.get(actor.account_id)
        if account is None:
            raise AccountNotFoundError
        return account

    async def update_current_account(
        self,
        actor: ActorContext,
        *,
        display_name: str | None,
    ) -> Account:
        if display_name is None:
            raise AccountValidationError("display_name cannot be null")
        account = await self.get_current_account(actor)
        self._account_repo.update_display_name(account, display_name=display_name)
        await self._session.commit()
        await self._session.refresh(account)
        return account
