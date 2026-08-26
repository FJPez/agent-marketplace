import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record, create_service_record

from app.core.enums import ServiceHealthStatus, ServiceLifecycle
from app.db.models import ServiceHealthCheck
from app.services.service_health_service import (
    PUBLISH_READINESS_CHECK_NAME,
    ServiceHealthOutcome,
    ServiceHealthService,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]


class StubChecker:
    def __init__(self, outcome: ServiceHealthOutcome) -> None:
        self._outcome = outcome

    async def run(self, *, service_id: int) -> ServiceHealthOutcome:
        _ = service_id
        return self._outcome


@pytest.fixture
async def service_id(db_session_factory: async_sessionmaker[AsyncSession]) -> int:
    provider_account_id = await create_provider_account_record(db_session_factory)
    return await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="health-checked-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )


async def test_record_check_is_invisible_to_other_sessions_until_caller_commits(
    db_session_factory: async_sessionmaker[AsyncSession],
    service_id: int,
) -> None:
    recording_session = db_session_factory()
    try:
        health_service = ServiceHealthService(recording_session)
        await health_service.record_check(
            service_id=service_id,
            check_name=PUBLISH_READINESS_CHECK_NAME,
            outcome=ServiceHealthOutcome(status=ServiceHealthStatus.PASS),
        )

        async with db_session_factory() as concurrent_session:
            concurrent_checks = await concurrent_session.scalars(
                select(ServiceHealthCheck).where(ServiceHealthCheck.service_id == service_id),
            )
            assert list(concurrent_checks.all()) == []
    finally:
        # Closing without committing rolls the flushed row back.
        await recording_session.close()

    async with db_session_factory() as session:
        rolled_back_checks = await session.scalars(
            select(ServiceHealthCheck).where(ServiceHealthCheck.service_id == service_id),
        )

    assert list(rolled_back_checks.all()) == []


async def test_record_check_row_is_durable_once_the_caller_commits(
    db_session_factory: async_sessionmaker[AsyncSession],
    service_id: int,
) -> None:
    async with db_session_factory() as session:
        health_service = ServiceHealthService(session)
        await health_service.record_check(
            service_id=service_id,
            check_name=PUBLISH_READINESS_CHECK_NAME,
            outcome=ServiceHealthOutcome(
                status=ServiceHealthStatus.FAIL,
                summary="upstream unavailable",
                details={"status_code": 503},
            ),
        )
        await session.commit()

    async with db_session_factory() as session:
        result = await session.scalars(
            select(ServiceHealthCheck).where(ServiceHealthCheck.service_id == service_id),
        )
        persisted_checks = list(result.all())

    assert len(persisted_checks) == 1
    assert persisted_checks[0].check_name == PUBLISH_READINESS_CHECK_NAME
    assert persisted_checks[0].status is ServiceHealthStatus.FAIL
    assert persisted_checks[0].summary == "upstream unavailable"
    assert persisted_checks[0].details == {"status_code": 503}


async def test_run_check_commits_the_checker_outcome_itself(
    db_session_factory: async_sessionmaker[AsyncSession],
    service_id: int,
) -> None:
    checker = StubChecker(
        ServiceHealthOutcome(
            status=ServiceHealthStatus.PASS,
            summary="probe passed",
            details={"latency_ms": 42},
        )
    )

    async with db_session_factory() as session:
        check = await ServiceHealthService(session).run_check(
            service_id=service_id,
            check_name=PUBLISH_READINESS_CHECK_NAME,
            checker=checker,
        )

        assert check.id is not None
        assert check.checked_at is not None

    async with db_session_factory() as session:
        result = await session.scalars(
            select(ServiceHealthCheck).where(ServiceHealthCheck.service_id == service_id),
        )
        persisted_checks = list(result.all())

    assert len(persisted_checks) == 1
    assert persisted_checks[0].id == check.id
    assert persisted_checks[0].status is ServiceHealthStatus.PASS
    assert persisted_checks[0].summary == "probe passed"
    assert persisted_checks[0].details == {"latency_ms": 42}
    assert persisted_checks[0].checked_at == check.checked_at


class RaisingChecker:
    async def run(self, *, service_id: int) -> ServiceHealthOutcome:
        _ = service_id
        msg = "probe timed out"
        raise RuntimeError(msg)


async def test_run_check_persists_sanitized_failure_when_checker_raises(
    db_session_factory: async_sessionmaker[AsyncSession],
    service_id: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with db_session_factory() as session:
        with caplog.at_level("ERROR"):
            check = await ServiceHealthService(session).run_check(
                service_id=service_id,
                check_name=PUBLISH_READINESS_CHECK_NAME,
                checker=RaisingChecker(),
            )

    async with db_session_factory() as session:
        result = await session.scalars(
            select(ServiceHealthCheck).where(ServiceHealthCheck.service_id == service_id),
        )
        persisted_checks = list(result.all())

    assert len(persisted_checks) == 1
    assert persisted_checks[0].id == check.id
    assert persisted_checks[0].status is ServiceHealthStatus.FAIL
    assert persisted_checks[0].summary == "health check failed"
    assert persisted_checks[0].details == {"error_type": "RuntimeError"}
    # The checker's internal error text must not leak into the stored row.
    assert "probe timed out" not in (persisted_checks[0].summary or "")
    assert any("service health check failed" in message for message in caplog.messages)
