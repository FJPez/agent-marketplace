from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.resources import open_resources


async def _open_connections(db_engine: AsyncEngine, application_name: str) -> int:
    async with db_engine.connect() as connection:
        count = await connection.scalar(
            text("SELECT count(*) FROM pg_stat_activity WHERE application_name = :name"),
            {"name": application_name},
        )
    return count or 0


async def test_closing_resources_closes_their_database_connections(
    db_settings: Settings,
    db_engine: AsyncEngine,
) -> None:
    application_name = f"resources-test-{uuid4().hex[:12]}"
    settings = db_settings.model_copy(update={"db_application_name": application_name})

    async with open_resources(settings) as resources:
        async with resources.db_session_factory() as session:
            await session.execute(text("SELECT 1"))
        # The pool keeps the connection open for the next session.
        assert await _open_connections(db_engine, application_name) == 1

    assert await _open_connections(db_engine, application_name) == 0
