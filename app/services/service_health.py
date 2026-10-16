"""Persistence for service health check rows."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ServiceHealthStatus
from app.core.json_types import JsonObject
from app.db.models import ServiceHealthCheck

PUBLISH_READINESS_CHECK_NAME = "publish-readiness"


@dataclass(frozen=True, slots=True)
class ServiceHealthOutcome:
    status: ServiceHealthStatus
    summary: str | None = None
    details: JsonObject | None = None


async def record_check(
    *,
    session: AsyncSession,
    service_id: int,
    check_name: str,
    outcome: ServiceHealthOutcome,
    checked_at: datetime,
) -> ServiceHealthCheck:
    check = ServiceHealthCheck(
        service_id=service_id,
        check_name=check_name,
        status=outcome.status,
        summary=outcome.summary,
        details=outcome.details,
        checked_at=checked_at,
    )
    session.add(check)
    # Participates in the caller's transaction: publishing owns the commit,
    # including its deliberate commit on rejection.
    await session.flush()
    return check
