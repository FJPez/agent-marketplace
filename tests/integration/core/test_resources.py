from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import app.core.resources as resources_module
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


async def test_open_resources_disposes_the_engine_when_a_later_resource_fails_to_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed = False

    class _FakeEngine:
        async def dispose(self) -> None:
            nonlocal disposed
            disposed = True

    monkeypatch.setattr(resources_module, "create_engine", lambda settings: _FakeEngine())

    def _raise_malformed_redis_url(*args: object, **kwargs: object) -> None:
        msg = "malformed redis url"
        raise ValueError(msg)

    monkeypatch.setattr(resources_module.coredis.Redis, "from_url", _raise_malformed_redis_url)

    settings = Settings(redis_url="redis://malformed")

    with pytest.raises(ValueError, match="malformed redis url"):
        async with open_resources(settings):
            pass

    assert disposed is True
