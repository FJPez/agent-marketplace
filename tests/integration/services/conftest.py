from collections.abc import Awaitable, Callable
from datetime import datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ApiKey

LastUsedAtReader = Callable[[int], Awaitable[datetime | None]]
LastUsedAtWriter = Callable[[int, datetime], Awaitable[None]]


@pytest.fixture
def stored_last_used_at(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> LastUsedAtReader:
    """Read `last_used_at` of the one API key of an account."""

    async def read(account_id: int) -> datetime | None:
        async with db_session_factory() as session:
            return await session.scalar(
                select(ApiKey.last_used_at).where(ApiKey.account_id == account_id),
            )

    return read


@pytest.fixture
def set_last_used_at(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> LastUsedAtWriter:
    """Overwrite `last_used_at` of the one API key of an account."""

    async def write(account_id: int, last_used_at: datetime) -> None:
        async with db_session_factory.begin() as session:
            await session.execute(
                update(ApiKey)
                .where(ApiKey.account_id == account_id)
                .values(last_used_at=last_used_at),
            )

    return write
