import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account, ModerationAction
from app.repositories.moderation_action_repo import ModerationActionRepository

SERVICE_ID = 101
OTHER_SERVICE_ID = 202


async def _create_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        return account.id


@pytest.mark.asyncio
async def test_moderation_action_repository_persists_action_with_actor(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    account_id = await _create_account(db_session_factory)

    async with db_session_factory.begin() as session:
        repo = ModerationActionRepository(session)
        record = repo.add(
            service_id=SERVICE_ID,
            actor_account_id=account_id,
            action="suspend",
            reason="spam",
        )

    async with db_session_factory() as session:
        persisted = await session.get(ModerationAction, record.id)

    assert persisted is not None
    assert persisted.service_id == SERVICE_ID
    assert persisted.actor_account_id == account_id
    assert persisted.action == "suspend"
    assert persisted.reason == "spam"
    assert persisted.created_at is not None


@pytest.mark.asyncio
async def test_moderation_action_repository_persists_action_without_actor(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        repo = ModerationActionRepository(session)
        record = repo.add(
            service_id=SERVICE_ID,
            actor_account_id=None,
            action="delist",
            reason="policy violation",
        )

    async with db_session_factory() as session:
        persisted = await session.get(ModerationAction, record.id)

    assert persisted is not None
    assert persisted.actor_account_id is None
    assert persisted.action == "delist"


@pytest.mark.asyncio
async def test_moderation_action_repository_lists_history_by_service_id(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    account_id = await _create_account(db_session_factory)

    async with db_session_factory.begin() as session:
        repo = ModerationActionRepository(session)
        repo.add(
            service_id=SERVICE_ID,
            actor_account_id=account_id,
            action="suspend",
            reason="spam",
        )
        repo.add(
            service_id=SERVICE_ID,
            actor_account_id=None,
            action="restore",
            reason="remediated",
        )
        repo.add(
            service_id=OTHER_SERVICE_ID,
            actor_account_id=None,
            action="delist",
            reason="policy violation",
        )

    async with db_session_factory() as session:
        repo = ModerationActionRepository(session)
        history = await repo.list_for_service(SERVICE_ID)

    assert [record.service_id for record in history] == [SERVICE_ID, SERVICE_ID]
    assert [record.action for record in history] == ["suspend", "restore"]
    assert [record.reason for record in history] == ["spam", "remediated"]


@pytest.mark.asyncio
async def test_moderation_action_repository_gets_latest_action_per_service(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        repo = ModerationActionRepository(session)
        repo.add(
            service_id=SERVICE_ID,
            actor_account_id=None,
            action="suspend",
            reason="spam",
        )
        repo.add(
            service_id=SERVICE_ID,
            actor_account_id=None,
            action="restore",
            reason="remediated",
        )
        repo.add(
            service_id=OTHER_SERVICE_ID,
            actor_account_id=None,
            action="delist",
            reason="policy violation",
        )

    async with db_session_factory() as session:
        repo = ModerationActionRepository(session)
        latest = await repo.get_latest_for_service(SERVICE_ID)
        other_latest = await repo.get_latest_for_service(OTHER_SERVICE_ID)
        missing_latest = await repo.get_latest_for_service(999)

    assert latest is not None
    assert latest.action == "restore"
    assert latest.service_id == SERVICE_ID
    assert other_latest is not None
    assert other_latest.action == "delist"
    assert other_latest.service_id == OTHER_SERVICE_ID
    assert missing_latest is None
