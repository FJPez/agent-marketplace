import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account, ConsumerProfile, ProviderProfile


@pytest.mark.asyncio
async def test_identity_models_persist_through_async_session(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()

        account_id = account.id
        session.add_all(
            [
                ProviderProfile(account_id=account_id, display_name="Provider"),
                ConsumerProfile(account_id=account_id, display_name="Consumer"),
            ],
        )

    async with db_session_factory() as session:
        persisted_account = await session.get(Account, account_id)
        provider_profile = await session.get(ProviderProfile, account_id)
        consumer_profile = await session.get(ConsumerProfile, account_id)

    assert persisted_account is not None
    assert provider_profile is not None
    assert consumer_profile is not None
    assert provider_profile.display_name == "Provider"
    assert consumer_profile.display_name == "Consumer"
