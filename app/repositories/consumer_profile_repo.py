from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConsumerProfile


class ConsumerProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_account_id(self, account_id: int) -> ConsumerProfile | None:
        return await self._session.get(ConsumerProfile, account_id)

    def add(self, *, account_id: int, display_name: str) -> ConsumerProfile:
        profile = ConsumerProfile(account_id=account_id, display_name=display_name)
        self._session.add(profile)
        return profile
