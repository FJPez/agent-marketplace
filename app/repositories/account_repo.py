from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        wallet_address: str,
        display_name: str = "Anonymous",
        account_type: str = "human",
        nonce: str = "",
        nonce_issued_at: datetime | None = None,
    ) -> Account:
        account = Account(
            wallet_address=wallet_address,
            display_name=display_name,
            account_type=account_type,
            nonce=nonce,
            nonce_issued_at=nonce_issued_at or datetime.now(UTC),
        )
        self._session.add(account)
        await self._session.flush()
        return account

    async def exists(self, account_id: int) -> bool:
        statement = select(Account.id).where(Account.id == account_id).limit(1)
        result = await self._session.scalar(statement)
        return result is not None

    async def get(self, account_id: int) -> Account | None:
        return await self._session.get(Account, account_id)

    async def get_by_wallet_address(self, wallet_address: str) -> Account | None:
        statement = select(Account).where(Account.wallet_address == wallet_address)
        return await self._session.scalar(statement)

    def update_nonce(
        self,
        account: Account,
        *,
        nonce: str,
        issued_at: datetime | None = None,
    ) -> Account:
        account.nonce = nonce
        account.nonce_issued_at = issued_at or datetime.now(UTC)
        account.updated_at = datetime.now(UTC)
        return account

    def update_display_name(self, account: Account, *, display_name: str) -> Account:
        account.display_name = display_name
        account.updated_at = datetime.now(UTC)
        return account

    def update_wallet(
        self,
        account: Account,
        *,
        wallet_address: str,
        wallet_changed_at: datetime | None = None,
    ) -> Account:
        account.wallet_address = wallet_address
        account.wallet_changed_at = wallet_changed_at or datetime.now(UTC)
        account.updated_at = datetime.now(UTC)
        return account

    def bump_token_version(self, account: Account) -> Account:
        account.token_version += 1
        account.updated_at = datetime.now(UTC)
        return account
