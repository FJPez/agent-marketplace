from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import ServiceHealthStatus
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
        repo = ServiceHealthCheckRepository(session)
        latest_check = await repo.get_latest_for_service_check(
            service_id=101,
            check_name="publish-readiness",
        )

    assert latest_check is not None
    assert latest_check.service_id == 101
    assert latest_check.check_name == "publish-readiness"
    assert latest_check.status is ServiceHealthStatus.PASS
    assert latest_check.summary == "Probe passed"
    assert latest_check.details == {"latency_ms": 42, "status_code": 200}
    assert latest_check.checked_at == checked_at


@pytest.mark.asyncio
async def test_service_health_check_repository_returns_latest_record_for_same_check_name(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        repo = ServiceHealthCheckRepository(session)
        repo.add(
            service_id=202,
            check_name="publish-readiness",
            status=ServiceHealthStatus.FAIL,
            summary="First failure",
            checked_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )
        repo.add(
            service_id=202,
            check_name="publish-readiness",
            status=ServiceHealthStatus.PASS,
            summary="Recovered",
            checked_at=datetime(2026, 3, 12, 12, 5, tzinfo=UTC),
        )

    async with db_session_factory() as session:
        repo = ServiceHealthCheckRepository(session)
        latest_check = await repo.get_latest_for_service_check(
            service_id=202,
            check_name="publish-readiness",
        )

    assert latest_check is not None
    assert latest_check.status is ServiceHealthStatus.PASS
    assert latest_check.summary == "Recovered"


@pytest.mark.asyncio
async def test_service_health_check_repository_scopes_latest_read_by_check_name(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        repo = ServiceHealthCheckRepository(session)
        repo.add(
            service_id=303,
            check_name="publish-readiness",
            status=ServiceHealthStatus.FAIL,
            summary="Publish probe failed",
            checked_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )
        repo.add(
            service_id=303,
            check_name="invoke-upstream",
            status=ServiceHealthStatus.PASS,
            summary="Invoke probe passed",
            checked_at=datetime(2026, 3, 12, 12, 10, tzinfo=UTC),
        )

    async with db_session_factory() as session:
        repo = ServiceHealthCheckRepository(session)
        latest_check = await repo.get_latest_for_service_check(
            service_id=303,
            check_name="publish-readiness",
        )

    assert latest_check is not None
    assert latest_check.check_name == "publish-readiness"
    assert latest_check.summary == "Publish probe failed"
