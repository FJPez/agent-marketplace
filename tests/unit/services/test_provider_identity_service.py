from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from app.core.actor import ActorContext
from app.db.models import ProviderProfile
from app.schemas.provider import (
    ProviderProfileCreateRequest,
    ProviderProfileUpdateRequest,
)
from app.services.identity_errors import (
    IdentityConflictError,
    IdentityNotFoundError,
    IdentityValidationError,
)
from app.services.provider_identity_service import ProviderIdentityService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: list[object] = []

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, instance: object) -> None:
        self.refreshed.append(instance)


class FakeProviderProfileRepository:
    def __init__(self, profile: ProviderProfile | None = None) -> None:
        self._profile = profile

    async def get_by_account_id(self, account_id: int) -> ProviderProfile | None:
        _ = account_id
        return self._profile

    def add(self, *, account_id: int, display_name: str) -> ProviderProfile:
        self._profile = ProviderProfile(
            account_id=account_id,
            display_name=display_name,
            created_at=datetime(2026, 3, 11, tzinfo=UTC),
        )
        return self._profile

    def update_display_name(
        self,
        profile: ProviderProfile,
        *,
        display_name: str,
    ) -> ProviderProfile:
        profile.display_name = display_name
        return profile


@pytest.mark.asyncio
async def test_create_profile_raises_conflict_when_profile_exists() -> None:
    session = FakeSession()
    repo = FakeProviderProfileRepository(
        profile=ProviderProfile(
            account_id=7,
            display_name="Existing",
            created_at=datetime(2026, 3, 11, tzinfo=UTC),
        )
    )
    service = ProviderIdentityService(
        cast("AsyncSession", session),
        provider_profile_repo=repo,
    )

    with pytest.raises(IdentityConflictError):
        await service.create_profile(
            ActorContext(account_id=7),
            ProviderProfileCreateRequest(display_name="New Name"),
        )

    assert session.commits == 0
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_get_profile_raises_not_found_for_missing_profile() -> None:
    service = ProviderIdentityService(
        cast("AsyncSession", FakeSession()),
        provider_profile_repo=FakeProviderProfileRepository(),
    )

    with pytest.raises(IdentityNotFoundError) as exc_info:
        await service.get_profile(ActorContext(account_id=11))

    assert exc_info.value.profile_type == "provider"
    assert exc_info.value.account_id == 11


@pytest.mark.asyncio
async def test_update_profile_rejects_empty_patch_payload() -> None:
    service = ProviderIdentityService(
        cast("AsyncSession", FakeSession()),
        provider_profile_repo=FakeProviderProfileRepository(
            profile=ProviderProfile(
                account_id=5,
                display_name="Existing",
                created_at=datetime(2026, 3, 11, tzinfo=UTC),
            )
        ),
    )

    with pytest.raises(
        IdentityValidationError,
        match="at least one field must be provided",
    ):
        await service.update_profile(
            ActorContext(account_id=5),
            ProviderProfileUpdateRequest(),
        )
