import asyncio

import pytest
from alembic import command
from tests.integration.db.support import require_test_database_url
from tests.integration.db.test_migrations import get_alembic_config

from app.core.config import Settings
from app.db.models import Account, ConsumerProfile, ProviderProfile
from app.db.session import create_engine, create_session_factory


@pytest.mark.asyncio
async def test_identity_models_persist_through_async_session() -> None:
    config = get_alembic_config()
    await asyncio.to_thread(command.upgrade, config, "head")

    settings = Settings(database_url=require_test_database_url(Settings().database_url))
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory.begin() as session:
            account = Account()
            session.add(account)
            await session.flush()

            account_id = account.id
            session.add_all(
                [
                    ProviderProfile(account_id=account_id),
                    ConsumerProfile(account_id=account_id),
                ],
            )

        async with session_factory() as session:
            persisted_account = await session.get(Account, account_id)
            provider_profile = await session.get(ProviderProfile, account_id)
            consumer_profile = await session.get(ConsumerProfile, account_id)
    finally:
        await engine.dispose()
        await asyncio.to_thread(command.downgrade, config, "base")

    assert persisted_account is not None
    assert provider_profile is not None
    assert consumer_profile is not None
