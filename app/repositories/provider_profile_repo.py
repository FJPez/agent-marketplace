from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProviderProfile


class ProviderProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_account_id(self, account_id: int) -> ProviderProfile | None:
        return await self._session.get(ProviderProfile, account_id)

    def add(self, *, account_id: int, display_name: str) -> ProviderProfile:
        profile = ProviderProfile(account_id=account_id, display_name=display_name)
        self._session.add(profile)
        return profile

    def update_display_name(
        self,
        profile: ProviderProfile,
        *,
        display_name: str,
    ) -> ProviderProfile:
        profile.display_name = display_name
        return profile
