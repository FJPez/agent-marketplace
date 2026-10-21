import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import create_engine


@pytest.mark.asyncio
async def test_session_factory_connects_to_postgres(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        result = await session.execute(text("SELECT 1"))

    assert result.scalar_one() == 1


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param(
            {},
            {
                "statement_timeout": "30s",
                "lock_timeout": "5s",
                "idle_in_transaction_session_timeout": "1min",
                "application_name": "agent-marketplace-api",
            },
            id="defaults",
        ),
        pytest.param(
            {
                "db_statement_timeout_ms": 1500,
                "db_lock_timeout_ms": 250,
                "db_idle_in_transaction_session_timeout_ms": 45000,
                "db_application_name": "agent-marketplace-smoke",
            },
            {
                "statement_timeout": "1500ms",
                "lock_timeout": "250ms",
                "idle_in_transaction_session_timeout": "45s",
                "application_name": "agent-marketplace-smoke",
            },
            id="configured",
        ),
    ],
)
async def test_engine_applies_session_settings_to_its_connections(
    db_settings: Settings,
    overrides: dict[str, int | str],
    expected: dict[str, str],
) -> None:
    engine = create_engine(db_settings.model_copy(update=overrides))
    try:
        async with engine.connect() as connection:
            shown = {name: await connection.scalar(text(f"SHOW {name}")) for name in expected}
    finally:
        await engine.dispose()

    assert shown == expected
