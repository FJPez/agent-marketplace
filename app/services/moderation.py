"""Moderation state derivation and admin moderation actions for services."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidStateError, NotFoundError
from app.db.models import ModerationAction, Service


class ModerationActionType(StrEnum):
    SUSPEND = "suspend"
    RESTORE = "restore"
    DELIST = "delist"


class ModerationServiceState(StrEnum):
    CLEAR = "clear"
    SUSPENDED = "suspended"
    DELISTED = "delisted"


class ServiceUnavailableError(InvalidStateError):
    """Moderation hides the service; callers decide how to surface it publicly."""

    def __init__(self, *, service_id: int, state: ModerationServiceState) -> None:
        self.service_id = service_id
        self.state = state
        super().__init__(f"service {service_id} is {state.value}")


STATE_AFTER_ACTION = {
    ModerationActionType.SUSPEND: ModerationServiceState.SUSPENDED,
    ModerationActionType.RESTORE: ModerationServiceState.CLEAR,
    ModerationActionType.DELIST: ModerationServiceState.DELISTED,
}

ALLOWED_ACTIONS = {
    ModerationServiceState.CLEAR: {
        ModerationActionType.SUSPEND,
        ModerationActionType.DELIST,
    },
    ModerationServiceState.SUSPENDED: {
        ModerationActionType.RESTORE,
        ModerationActionType.DELIST,
    },
    ModerationServiceState.DELISTED: {
        ModerationActionType.RESTORE,
        ModerationActionType.SUSPEND,
    },
}


async def get_service_state(
    *,
    session: AsyncSession,
    service_id: int,
) -> ModerationServiceState:
    """Return the state implied by the most recent moderation action."""
    latest_action = await session.scalar(
        select(ModerationAction.action)
        .where(ModerationAction.service_id == service_id)
        .order_by(ModerationAction.id.desc())
        .limit(1),
    )
    if latest_action is None:
        return ModerationServiceState.CLEAR
    return STATE_AFTER_ACTION[ModerationActionType(latest_action)]


async def ensure_service_available(*, session: AsyncSession, service_id: int) -> None:
    """Reject suspended and delisted services on public read paths."""
    state = await get_service_state(session=session, service_id=service_id)
    if state is ModerationServiceState.CLEAR:
        return
    raise ServiceUnavailableError(service_id=service_id, state=state)


async def ensure_service_publishable(*, session: AsyncSession, service_id: int) -> None:
    """Reject suspended services on provider write paths; delisted services may still publish."""
    state = await get_service_state(session=session, service_id=service_id)
    if state is ModerationServiceState.SUSPENDED:
        raise ServiceUnavailableError(service_id=service_id, state=state)


async def get_unlisted_service_ids(
    *,
    session: AsyncSession,
    service_ids: list[int],
) -> set[int]:
    """Return the ids whose latest moderation action leaves them hidden from the catalogue."""
    if not service_ids:
        return set()

    latest_action_ids = (
        select(func.max(ModerationAction.id))
        .where(ModerationAction.service_id.in_(service_ids))
        .group_by(ModerationAction.service_id)
        .scalar_subquery()
    )
    result = await session.scalars(
        select(ModerationAction.service_id).where(
            ModerationAction.id.in_(latest_action_ids),
            ModerationAction.action != ModerationActionType.RESTORE.value,
        ),
    )
    return set(result)


async def list_actions(*, session: AsyncSession, service_id: int) -> list[ModerationAction]:
    """Return the moderation history of a service, oldest action first."""
    result = await session.scalars(
        select(ModerationAction)
        .where(ModerationAction.service_id == service_id)
        .order_by(ModerationAction.id.asc()),
    )
    return list(result)


async def suspend_service(
    *,
    session: AsyncSession,
    service_id: int,
    actor_account_id: int,
    reason: str,
) -> ModerationAction:
    """Hide a service from every marketplace path until it is restored."""
    return await _record_action(
        session=session,
        service_id=service_id,
        actor_account_id=actor_account_id,
        reason=reason,
        action=ModerationActionType.SUSPEND,
    )


async def restore_service(
    *,
    session: AsyncSession,
    service_id: int,
    actor_account_id: int,
    reason: str,
) -> ModerationAction:
    """Clear the moderation state of a suspended or delisted service."""
    return await _record_action(
        session=session,
        service_id=service_id,
        actor_account_id=actor_account_id,
        reason=reason,
        action=ModerationActionType.RESTORE,
    )


async def delist_service(
    *,
    session: AsyncSession,
    service_id: int,
    actor_account_id: int,
    reason: str,
) -> ModerationAction:
    """Hide a service from public discovery while leaving provider writes open."""
    return await _record_action(
        session=session,
        service_id=service_id,
        actor_account_id=actor_account_id,
        reason=reason,
        action=ModerationActionType.DELIST,
    )


async def _record_action(
    *,
    session: AsyncSession,
    service_id: int,
    actor_account_id: int,
    reason: str,
    action: ModerationActionType,
) -> ModerationAction:
    locked_service_id = await session.scalar(
        select(Service.id).where(Service.id == service_id).with_for_update(),
    )
    if locked_service_id is None:
        raise NotFoundError("service not found")

    state = await get_service_state(session=session, service_id=service_id)
    if not _is_valid_transition(state, action):
        msg = f"cannot {action.value} service {service_id} from {state.value}"
        raise InvalidStateError(msg)

    now = datetime.now(UTC)
    record = ModerationAction(
        service_id=service_id,
        actor_account_id=actor_account_id,
        action=action.value,
        reason=reason,
        created_at=now,
    )
    session.add(record)
    await session.flush()
    await session.commit()
    return record


def _is_valid_transition(
    state: ModerationServiceState,
    action: ModerationActionType,
) -> bool:
    return action in ALLOWED_ACTIONS[state]
