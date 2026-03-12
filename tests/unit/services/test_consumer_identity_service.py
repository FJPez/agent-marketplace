from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from app.core.actor import ActorContext
from app.db.models import Account, ConsumerProfile
from app.schemas.consumer import ConsumerProfileCreateRequest
from app.services.consumer_identity_service import ConsumerIdentityService
from app.services.identity_errors import IdentityConflictError

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


class FakeAccountRepository:
    def __init__(self, *, next_account_id: int = 101) -> None:
        self.created = 0
        self.next_account_id = next_account_id

    async def create(self) -> Account:
        self.created += 1
        return Account(
            id=self.next_account_id,
            created_at=datetime(2026, 3, 11, tzinfo=UTC),
        )


class FakeConsumerProfileRepository:
    def __init__(self, profile: ConsumerProfile | None = None) -> None:
        self._profile = profile

    async def get_by_account_id(self, account_id: int) -> ConsumerProfile | None:
        _ = account_id
        return self._profile

    def add(self, *, account_id: int, display_name: str) -> ConsumerProfile:
        self._profile = ConsumerProfile(
            account_id=account_id,
            display_name=display_name,
            created_at=datetime(2026, 3, 11, tzinfo=UTC),
        )
        return self._profile


@pytest.mark.asyncio
async def test_create_profile_bootstraps_account_when_actor_missing() -> None:
    session = FakeSession()
    account_repo = FakeAccountRepository(next_account_id=202)
    service = ConsumerIdentityService(
        cast("AsyncSession", session),
        account_repo=account_repo,
        consumer_profile_repo=FakeConsumerProfileRepository(),
    )

    profile = await service.create_profile(
        None,
        ConsumerProfileCreateRequest(display_name="Bootstrap Consumer"),
    )

    assert profile.account_id == 202
    assert profile.display_name == "Bootstrap Consumer"
    assert account_repo.created == 1
    assert session.commits == 1
    assert session.refreshed == [profile]


@pytest.mark.asyncio
async def test_create_profile_uses_existing_actor_without_creating_account() -> None:
    session = FakeSession()
    account_repo = FakeAccountRepository(next_account_id=303)
    service = ConsumerIdentityService(
        cast("AsyncSession", session),
        account_repo=account_repo,
        consumer_profile_repo=FakeConsumerProfileRepository(),
    )

    profile = await service.create_profile(
        ActorContext(account_id=77),
        ConsumerProfileCreateRequest(display_name="Existing Account Consumer"),
    )

    assert profile.account_id == 77
    assert profile.display_name == "Existing Account Consumer"
    assert account_repo.created == 0
    assert session.commits == 1
    assert session.refreshed == [profile]


@pytest.mark.asyncio
async def test_create_profile_raises_conflict_when_profile_exists() -> None:
    session = FakeSession()
    service = ConsumerIdentityService(
        cast("AsyncSession", session),
        account_repo=FakeAccountRepository(),
        consumer_profile_repo=FakeConsumerProfileRepository(
            profile=ConsumerProfile(
                account_id=7,
                display_name="Existing",
                created_at=datetime(2026, 3, 11, tzinfo=UTC),
            )
        ),
    )

    with pytest.raises(IdentityConflictError):
        await service.create_profile(
            ActorContext(account_id=7),
            ConsumerProfileCreateRequest(display_name="New Consumer"),
        )

    assert session.commits == 0
    assert session.rollbacks == 0
