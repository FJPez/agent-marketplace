import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.db.session import create_engine, create_session_factory


@pytest.mark.asyncio
async def test_session_factory_connects_to_postgres() -> None:
    settings = Settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            result = await session.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

    assert result.scalar_one() == 1
