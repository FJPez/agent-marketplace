from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.lifespan import get_resources


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return get_resources(request.app).db_session_factory


SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


async def get_db_session(session_factory: SessionFactoryDep) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
