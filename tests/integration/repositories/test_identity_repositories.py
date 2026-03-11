import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account
from app.repositories.consumer_profile_repo import ConsumerProfileRepository
from app.repositories.provider_profile_repo import ProviderProfileRepository


@pytest.mark.asyncio
async def test_provider_profile_repository_persists_and_updates_display_name(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()

        repo = ProviderProfileRepository(session)
        repo.add(account_id=account.id, display_name="Alpha Provider")

    async with db_session_factory() as session:
        repo = ProviderProfileRepository(session)
        profile = await repo.get_by_account_id(account.id)

        assert profile is not None
        assert profile.display_name == "Alpha Provider"

        repo.update_display_name(profile, display_name="Bravo Provider")
        await session.commit()

    async with db_session_factory() as session:
        repo = ProviderProfileRepository(session)
        updated_profile = await repo.get_by_account_id(account.id)

    assert updated_profile is not None
    assert updated_profile.display_name == "Bravo Provider"


@pytest.mark.asyncio
async def test_consumer_profile_repository_persists_display_name(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()

        repo = ConsumerProfileRepository(session)
        repo.add(account_id=account.id, display_name="Consumer One")

    async with db_session_factory() as session:
        repo = ConsumerProfileRepository(session)
        profile = await repo.get_by_account_id(account.id)

    assert profile is not None
    assert profile.display_name == "Consumer One"
