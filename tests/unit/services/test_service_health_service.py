from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from app.core.enums import ServiceHealthStatus
from app.db.models import ServiceHealthCheck
from app.services.service_health_service import (
    ServiceHealthOutcome,
    ServiceHealthService,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.refreshed: list[object] = []

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, instance: object) -> None:
        self.refreshed.append(instance)


class FakeServiceHealthCheckRepository:
    def __init__(self) -> None:
        self.added: list[dict[str, object]] = []
        self.latest_check: ServiceHealthCheck | None = None

    def add(
        self,
        *,
        service_id: int,
        check_name: str,
        status: ServiceHealthStatus,
        summary: str | None = None,
        details: dict[str, object] | None = None,
        checked_at: datetime | None = None,
    ) -> ServiceHealthCheck:
        self.added.append(
            {
                "service_id": service_id,
                "check_name": check_name,
                "status": status,
                "summary": summary,
                "details": details,
                "checked_at": checked_at,
            }
        )
        check = ServiceHealthCheck(
            id=len(self.added),
            service_id=service_id,
            check_name=check_name,
            status=status,
            summary=summary,
            details=details,
            checked_at=checked_at or datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )
        self.latest_check = check
        return check

    async def get_latest_for_service_check(
        self,
        *,
        service_id: int,
        check_name: str,
    ) -> ServiceHealthCheck | None:
        _ = service_id
        _ = check_name
        return self.latest_check


class PassingChecker:
    async def run(self, *, service_id: int) -> ServiceHealthOutcome:
        return ServiceHealthOutcome(
            status=ServiceHealthStatus.PASS,
            summary=f"service {service_id} healthy",
            details={"latency_ms": 12},
        )


class FailingChecker:
    async def run(self, *, service_id: int) -> ServiceHealthOutcome:
        _ = service_id
        raise RuntimeError("probe timed out")


@pytest.mark.asyncio
async def test_record_check_persists_supplied_outcome() -> None:
    session = FakeSession()
    repo = FakeServiceHealthCheckRepository()
    service = ServiceHealthService(
        cast("AsyncSession", session),
        service_health_check_repo=repo,
    )

    check = await service.record_check(
        service_id=123,
        check_name="publish-readiness",
        outcome=ServiceHealthOutcome(
            status=ServiceHealthStatus.FAIL,
            summary="upstream unavailable",
            details={"status_code": 503},
        ),
    )

    assert check.service_id == 123
    assert check.check_name == "publish-readiness"
    assert check.status is ServiceHealthStatus.FAIL
    assert check.summary == "upstream unavailable"
    assert check.details == {"status_code": 503}
    assert session.commits == 1
    assert session.refreshed == [check]


@pytest.mark.asyncio
async def test_run_check_persists_successful_checker_outcome() -> None:
    session = FakeSession()
    repo = FakeServiceHealthCheckRepository()
    service = ServiceHealthService(
        cast("AsyncSession", session),
        service_health_check_repo=repo,
    )

    check = await service.run_check(
        service_id=55,
        check_name="publish-readiness",
        checker=PassingChecker(),
    )

    assert check.status is ServiceHealthStatus.PASS
    assert check.summary == "service 55 healthy"
    assert check.details == {"latency_ms": 12}
    assert session.commits == 1
    assert session.refreshed == [check]


@pytest.mark.asyncio
async def test_run_check_persists_error_outcome_when_checker_raises() -> None:
    session = FakeSession()
    repo = FakeServiceHealthCheckRepository()
    service = ServiceHealthService(
        cast("AsyncSession", session),
        service_health_check_repo=repo,
    )

    check = await service.run_check(
        service_id=89,
        check_name="publish-readiness",
        checker=FailingChecker(),
    )

    assert check.status is ServiceHealthStatus.ERROR
    assert check.summary == "probe timed out"
    assert check.details == {"error_type": "RuntimeError"}
    assert session.commits == 1
    assert session.refreshed == [check]


@pytest.mark.asyncio
async def test_get_latest_check_delegates_to_repository() -> None:
    repo = FakeServiceHealthCheckRepository()
    repo.latest_check = ServiceHealthCheck(
        id=7,
        service_id=901,
        check_name="publish-readiness",
        status=ServiceHealthStatus.PASS,
        summary="healthy",
        details=None,
        checked_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
    )
    service = ServiceHealthService(
        cast("AsyncSession", FakeSession()),
        service_health_check_repo=repo,
    )

    latest_check = await service.get_latest_check(
        service_id=901,
        check_name="publish-readiness",
    )

    assert latest_check is repo.latest_check
