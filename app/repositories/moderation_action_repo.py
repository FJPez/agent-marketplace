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

    async def get_latest_for_services(
        self,
        service_ids: list[int],
    ) -> dict[int, ModerationAction]:
        if not service_ids:
            return {}
        from sqlalchemy import func

        subquery = (
            select(
                ModerationAction.service_id,
                func.max(ModerationAction.id).label("max_id"),
            )
            .where(ModerationAction.service_id.in_(service_ids))
            .group_by(ModerationAction.service_id)
            .subquery()
        )
        statement = select(ModerationAction).join(
            subquery,
            ModerationAction.id == subquery.c.max_id,
        )
        result = await self._session.scalars(statement)
        return {action.service_id: action for action in result}
