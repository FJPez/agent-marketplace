from collections.abc import Mapping
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ServiceHealthStatus
from app.db.models import ServiceHealthCheck


class ServiceHealthCheckRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        service_id: int,
        check_name: str,
        status: ServiceHealthStatus,
        summary: str | None = None,
        details: Mapping[str, object] | None = None,
        checked_at: datetime | None = None,
    ) -> ServiceHealthCheck:
        check_kwargs: dict[str, object] = {
            "service_id": service_id,
            "check_name": check_name,
            "status": status,
            "summary": summary,
            "details": None if details is None else dict(details),
        }
        if checked_at is not None:
            check_kwargs["checked_at"] = checked_at

        check = ServiceHealthCheck(**check_kwargs)
        self._session.add(check)
        return check
