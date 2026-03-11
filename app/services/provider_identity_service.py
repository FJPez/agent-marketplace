from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.db.models import Account, ProviderProfile
from app.repositories.account_repo import AccountRepository
from app.repositories.provider_profile_repo import ProviderProfileRepository
from app.schemas.provider import (
    ProviderProfileCreateRequest,
    ProviderProfileUpdateRequest,
)
from app.services.identity_errors import (
    IdentityConflictError,
    IdentityNotFoundError,
    IdentityValidationError,
)


class ProviderProfileStore(Protocol):
    async def get_by_account_id(self, account_id: int) -> ProviderProfile | None: ...

    def add(self, *, account_id: int, display_name: str) -> ProviderProfile: ...

    def update_display_name(
        self,
        profile: ProviderProfile,
        *,
        display_name: str,
    ) -> ProviderProfile: ...


class AccountStore(Protocol):
    async def create(self) -> Account: ...


class ProviderIdentityService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        account_repo: AccountStore | None = None,
        provider_profile_repo: ProviderProfileStore | None = None,
    ) -> None:
        self._session = session
        self._account_repo = account_repo or AccountRepository(session)
        self._provider_profile_repo = provider_profile_repo or ProviderProfileRepository(
            session,
        )

    async def create_profile(
        self,
        actor: ActorContext | None,
        request: ProviderProfileCreateRequest,
    ) -> ProviderProfile:
        resolved_actor = actor
        if resolved_actor is None:
            account = await self._account_repo.create()
            resolved_actor = ActorContext(account_id=account.id)
        else:
            existing_profile = await self._provider_profile_repo.get_by_account_id(
                resolved_actor.account_id,
            )
            if existing_profile is not None:
                raise IdentityConflictError("provider profile already exists")

        profile = self._provider_profile_repo.add(
            account_id=resolved_actor.account_id,
            display_name=request.display_name,
        )

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdentityConflictError("provider profile already exists") from exc

        await self._session.refresh(profile)
        return profile

    async def get_profile(self, actor: ActorContext) -> ProviderProfile:
        profile = await self._provider_profile_repo.get_by_account_id(actor.account_id)
        if profile is None:
            raise IdentityNotFoundError(
                profile_type="provider",
                account_id=actor.account_id,
            )
        return profile

    async def update_profile(
        self,
        actor: ActorContext,
        request: ProviderProfileUpdateRequest,
    ) -> ProviderProfile:
        if not request.model_fields_set:
            raise IdentityValidationError("at least one field must be provided")

        profile = await self.get_profile(actor)

        if "display_name" in request.model_fields_set:
            if request.display_name is None:
                raise IdentityValidationError("display_name cannot be null")
            self._provider_profile_repo.update_display_name(
                profile,
                display_name=request.display_name,
            )

        await self._session.commit()
        await self._session.refresh(profile)
        return profile
