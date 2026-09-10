from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record, create_service_record

from app.core.enums import ServiceHealthStatus, ServiceLifecycle
from app.db.models import ServiceHealthCheck
from app.repositories.service_health_check_repo import ServiceHealthCheckRepository


@pytest.mark.asyncio
async def test_service_health_check_repository_persists_details_payload(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    checked_at = datetime(2026, 3, 12, 12, 30, tzinfo=UTC)
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="repo-health-checked-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory.begin() as session:
        repo = ServiceHealthCheckRepository(session)
        check = repo.add(
            service_id=service_id,
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
            select(ServiceHealthCheck).where(ServiceHealthCheck.service_id == service_id),
        )

    assert persisted_check is not None
    assert persisted_check.check_name == "publish-readiness"
    assert persisted_check.status is ServiceHealthStatus.PASS
    assert persisted_check.summary == "Probe passed"
    assert persisted_check.details == {"latency_ms": 42, "status_code": 200}
    assert persisted_check.checked_at == checked_at
