from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WalletChangeLog


class WalletChangeLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        account_id: int,
        previous_wallet_address: str,
        new_wallet_address: str,
    ) -> WalletChangeLog:
        log = WalletChangeLog(
            account_id=account_id,
            previous_wallet_address=previous_wallet_address,
            new_wallet_address=new_wallet_address,
        )
        self._session.add(log)
        return log
