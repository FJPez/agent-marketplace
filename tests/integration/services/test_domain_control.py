import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record

from app.services import domain_control


async def _ensure(db_session_factory: async_sessionmaker[AsyncSession], account_id: int) -> str:
    async with db_session_factory() as session:
        return await domain_control.ensure_domain_token(session=session, account_id=account_id)


async def test_a_domain_token_is_created_once_and_then_returned_unchanged(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    first = await _ensure(db_session_factory, account_id)
    second = await _ensure(db_session_factory, account_id)

    assert len(first) == 43
    assert second == first


async def test_concurrent_first_requests_agree_on_one_token(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    tokens = await asyncio.gather(*(_ensure(db_session_factory, account_id) for _ in range(5)))

    assert len(set(tokens)) == 1


async def test_each_account_gets_its_own_token(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_account_id = await create_provider_account_record(db_session_factory)
    second_account_id = await create_provider_account_record(db_session_factory)

    assert await _ensure(db_session_factory, first_account_id) != await _ensure(
        db_session_factory,
        second_account_id,
    )
