from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.db.models import ConsumerProfile
from app.repositories.consumer_profile_repo import ConsumerProfileRepository
from app.schemas.consumer import ConsumerProfileCreateRequest
from app.services.identity_errors import IdentityConflictError


class ConsumerProfileStore(Protocol):
    async def get_by_account_id(self, account_id: int) -> ConsumerProfile | None: ...

    def add(self, *, account_id: int, display_name: str) -> ConsumerProfile: ...


class ConsumerIdentityService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        consumer_profile_repo: ConsumerProfileStore | None = None,
    ) -> None:
        self._session = session
        self._consumer_profile_repo = consumer_profile_repo or ConsumerProfileRepository(
            session,
        )

    async def create_profile(
        self,
        actor: ActorContext,
        request: ConsumerProfileCreateRequest,
    ) -> ConsumerProfile:
        existing_profile = await self._consumer_profile_repo.get_by_account_id(
            actor.account_id,
        )
        if existing_profile is not None:
            raise IdentityConflictError("consumer profile already exists")

        profile = self._consumer_profile_repo.add(
            account_id=actor.account_id,
            display_name=request.display_name,
        )

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdentityConflictError("consumer profile already exists") from exc

        await self._session.refresh(profile)
        return profile
