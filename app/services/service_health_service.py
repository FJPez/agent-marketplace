from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ServiceHealthStatus
from app.core.logging import SERVICE_ID_FIELD, get_logger
from app.db.models import ServiceHealthCheck
from app.repositories.service_health_check_repo import ServiceHealthCheckRepository

logger = get_logger(__name__)
PUBLISH_READINESS_CHECK_NAME = "publish-readiness"
HEALTH_CHECK_FAILURE_SUMMARY = "health check failed"


@dataclass(frozen=True, slots=True)
class ServiceHealthOutcome:
    status: ServiceHealthStatus
    summary: str | None = None
    details: dict[str, object] | None = None
    checked_at: datetime | None = None


class ServiceHealthChecker(Protocol):
    async def run(self, *, service_id: int) -> ServiceHealthOutcome: ...


class ServiceHealthCheckStore(Protocol):
    def add(
        self,
        *,
        service_id: int,
        check_name: str,
        status: ServiceHealthStatus,
        summary: str | None = None,
        details: dict[str, object] | None = None,
        checked_at: datetime | None = None,
    ) -> ServiceHealthCheck: ...

    async def get_latest_for_service_check(
        self,
        *,
        service_id: int,
        check_name: str,
    ) -> ServiceHealthCheck | None: ...


class ServiceHealthCheckFailedError(Exception):
    def __init__(self, *, service_id: int, status: ServiceHealthStatus) -> None:
        self.service_id = service_id
        self.status = status
        super().__init__("latest health check is not passing")


class ServiceHealthService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        service_health_check_repo: ServiceHealthCheckStore | None = None,
    ) -> None:
        self._session = session
        self._service_health_check_repo = service_health_check_repo or (
            ServiceHealthCheckRepository(session)
        )

    async def record_check(
        self,
        *,
        service_id: int,
        check_name: str,
        outcome: ServiceHealthOutcome,
    ) -> ServiceHealthCheck:
        check = self._service_health_check_repo.add(
            service_id=service_id,
            check_name=check_name,
            status=outcome.status,
            summary=outcome.summary,
            details=outcome.details,
            checked_at=outcome.checked_at,
        )
        await self._session.commit()
        await self._session.refresh(check)
        return check

    async def run_check(
        self,
        *,
        service_id: int,
        check_name: str,
        checker: ServiceHealthChecker,
    ) -> ServiceHealthCheck:
        try:
            outcome = await checker.run(service_id=service_id)
        except Exception as exc:
            logger.exception(
                "service health check failed",
                extra={SERVICE_ID_FIELD: service_id, "check_name": check_name},
            )
            outcome = ServiceHealthOutcome(
                status=ServiceHealthStatus.ERROR,
                summary=HEALTH_CHECK_FAILURE_SUMMARY,
                details={"error_type": exc.__class__.__name__},
            )

        return await self.record_check(
            service_id=service_id,
            check_name=check_name,
            outcome=outcome,
        )

    async def get_latest_check(
        self,
        *,
        service_id: int,
        check_name: str,
    ) -> ServiceHealthCheck | None:
        return await self._service_health_check_repo.get_latest_for_service_check(
            service_id=service_id,
            check_name=check_name,
        )

    async def ensure_publish_ready(self, *, service_id: int) -> None:
        latest_check = await self.get_latest_check(
            service_id=service_id,
            check_name=PUBLISH_READINESS_CHECK_NAME,
        )
        if latest_check is None or latest_check.status is ServiceHealthStatus.PASS:
            return
        raise ServiceHealthCheckFailedError(
            service_id=service_id,
            status=latest_check.status,
        )
