from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from app.core.enums import ServiceHealthStatus
from app.db.models import ServiceHealthCheck
from app.services.service_health_service import (
    PUBLISH_READINESS_CHECK_NAME,
    ServiceHealthOutcome,
    ServiceHealthService,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeSession:
    """Transaction behaviour is covered by tests/integration/services/test_service_health.py."""

    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def refresh(self, instance: object) -> None:
        _ = instance


class FakeServiceHealthCheckRepository:
    def __init__(self) -> None:
        self.added: list[dict[str, object]] = []

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
        return ServiceHealthCheck(
            id=len(self.added),
            service_id=service_id,
            check_name=check_name,
            status=status,
            summary=summary,
            details=details,
            checked_at=checked_at or datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )


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
async def test_run_check_persists_successful_checker_outcome() -> None:
    service = ServiceHealthService(
        cast("AsyncSession", FakeSession()),
        service_health_check_repo=FakeServiceHealthCheckRepository(),
    )

    check = await service.run_check(
        service_id=55,
        check_name="publish-readiness",
        checker=PassingChecker(),
    )

    assert check.status is ServiceHealthStatus.PASS
    assert check.summary == "service 55 healthy"
    assert check.details == {"latency_ms": 12}


@pytest.mark.asyncio
async def test_run_check_persists_failed_outcome_when_checker_raises() -> None:
    service = ServiceHealthService(
        cast("AsyncSession", FakeSession()),
        service_health_check_repo=FakeServiceHealthCheckRepository(),
    )

    check = await service.run_check(
        service_id=89,
        check_name="publish-readiness",
        checker=FailingChecker(),
    )

    assert check.status is ServiceHealthStatus.FAIL
    assert check.summary == "health check failed"
    assert check.details == {"error_type": "RuntimeError"}


@pytest.mark.asyncio
async def test_run_check_logs_and_sanitizes_checker_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = ServiceHealthService(
        cast("AsyncSession", FakeSession()),
        service_health_check_repo=FakeServiceHealthCheckRepository(),
    )

    with caplog.at_level("ERROR"):
        check = await service.run_check(
            service_id=90,
            check_name=PUBLISH_READINESS_CHECK_NAME,
            checker=FailingChecker(),
        )

    assert check.summary == "health check failed"
    assert "probe timed out" not in (check.summary or "")
    assert any("service health check failed" in message for message in caplog.messages)
