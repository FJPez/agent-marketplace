from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.service_revision import ServiceRevision


class ServiceRevisionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_revision_number(self, *, service_id: int) -> int:
        current_revision_number = await self._session.scalar(
            select(func.max(ServiceRevision.revision_number)).where(
                ServiceRevision.service_id == service_id,
            ),
        )
        return (current_revision_number or 0) + 1

    def add(
        self,
        *,
        service_id: int,
        revision_number: int,
        change_token: str,
        snapshot: dict[str, object],
    ) -> ServiceRevision:
        revision = ServiceRevision(
            service_id=service_id,
            revision_number=revision_number,
            change_token=change_token,
            snapshot=snapshot,
        )
        self._session.add(revision)
        return revision

    async def list_by_service_id(self, *, service_id: int) -> list[ServiceRevision]:
        result = await self._session.scalars(
            select(ServiceRevision)
            .where(ServiceRevision.service_id == service_id)
            .order_by(
                ServiceRevision.revision_number.desc(),
                ServiceRevision.id.desc(),
            ),
        )
        return list(result.all())
