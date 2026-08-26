from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import ServiceHealthStatus
from app.db.models import ServiceHealthCheck
from app.repositories.service_health_check_repo import ServiceHealthCheckRepository


@pytest.mark.asyncio
async def test_service_health_check_repository_persists_details_payload(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    checked_at = datetime(2026, 3, 12, 12, 30, tzinfo=UTC)

    async with db_session_factory.begin() as session:
        repo = ServiceHealthCheckRepository(session)
        check = repo.add(
            service_id=101,
            check_name="publish-readiness",
            status=ServiceHealthStatus.PASS,
            summary="Probe passed",
            details={"latency_ms": 42, "status_code": 200},
            checked_at=checked_at,
        )
        await session.flush()

        assert check.id is not None

    async with db_session_factory() as session:
        persisted_check = await session.scalar(
            select(ServiceHealthCheck).where(ServiceHealthCheck.service_id == 101),
        )

    assert persisted_check is not None
    assert persisted_check.check_name == "publish-readiness"
    assert persisted_check.status is ServiceHealthStatus.PASS
    assert persisted_check.summary == "Probe passed"
    assert persisted_check.details == {"latency_ms": 42, "status_code": 200}
    assert persisted_check.checked_at == checked_at
