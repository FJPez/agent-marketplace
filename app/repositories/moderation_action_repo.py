from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModerationAction


class ModerationActionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        service_id: int,
        actor_account_id: int | None,
        action: str,
        reason: str,
    ) -> ModerationAction:
        record = ModerationAction(
            service_id=service_id,
            actor_account_id=actor_account_id,
            action=action,
            reason=reason,
        )
        self._session.add(record)
        return record

    async def list_for_service(self, service_id: int) -> list[ModerationAction]:
        statement = (
            select(ModerationAction)
            .where(ModerationAction.service_id == service_id)
            .order_by(ModerationAction.id.asc())
        )
        result = await self._session.scalars(statement)
        return list(result)

    async def get_latest_for_service(self, service_id: int) -> ModerationAction | None:
        statement = (
            select(ModerationAction)
            .where(ModerationAction.service_id == service_id)
            .order_by(ModerationAction.id.desc())
            .limit(1)
        )
        return await self._session.scalar(statement)
