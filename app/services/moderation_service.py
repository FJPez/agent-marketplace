from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext


class ModerationActionRecord(Protocol):
    service_id: int
    actor_account_id: int | None
    action: str
    reason: str


class ModerationActionStore(Protocol):
    async def get_latest_for_service(
        self,
        service_id: int,
    ) -> ModerationActionRecord | None: ...

    def add(
        self,
        *,
        service_id: int,
        actor_account_id: int | None,
        action: str,
        reason: str,
    ) -> ModerationActionRecord: ...


class ModerationActionType(StrEnum):
    SUSPEND = "suspend"
    RESTORE = "restore"
    DELIST = "delist"


class ModerationServiceState(StrEnum):
    CLEAR = "clear"
    SUSPENDED = "suspended"
    DELISTED = "delisted"


class InvalidModerationTransitionError(Exception):
    def __init__(
        self,
        *,
        service_id: int,
        current_state: ModerationServiceState,
        attempted_action: ModerationActionType,
    ) -> None:
        self.service_id = service_id
        self.current_state = current_state
        self.attempted_action = attempted_action
        super().__init__(
            f"cannot {attempted_action.value} service {service_id} from {current_state.value}",
        )


class ServiceUnavailableError(Exception):
    def __init__(
        self,
        *,
        service_id: int,
        state: ModerationServiceState,
    ) -> None:
        self.service_id = service_id
        self.state = state
        super().__init__(f"service {service_id} is {state.value}")


class ModerationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        moderation_action_repo: ModerationActionStore | None = None,
    ) -> None:
        self._session = session
        if moderation_action_repo is None:
            from app.repositories.moderation_action_repo import ModerationActionRepository

            moderation_action_repo = ModerationActionRepository(session)
        self._moderation_action_repo = moderation_action_repo

    async def suspend_service(
        self,
        *,
        service_id: int,
        reason: str,
        actor: ActorContext | None,
    ) -> ModerationActionRecord:
        return await self._record_action(
            service_id=service_id,
            reason=reason,
            actor=actor,
            action=ModerationActionType.SUSPEND,
        )

    async def restore_service(
        self,
        *,
        service_id: int,
        reason: str,
        actor: ActorContext | None,
    ) -> ModerationActionRecord:
        return await self._record_action(
            service_id=service_id,
            reason=reason,
            actor=actor,
            action=ModerationActionType.RESTORE,
        )

    async def delist_service(
        self,
        *,
        service_id: int,
        reason: str,
        actor: ActorContext | None,
    ) -> ModerationActionRecord:
        return await self._record_action(
            service_id=service_id,
            reason=reason,
            actor=actor,
            action=ModerationActionType.DELIST,
        )

    async def get_service_state(self, service_id: int) -> ModerationServiceState:
        latest_action = await self._moderation_action_repo.get_latest_for_service(service_id)
        if latest_action is None:
            return ModerationServiceState.CLEAR

        if latest_action.action == ModerationActionType.SUSPEND.value:
            return ModerationServiceState.SUSPENDED
        if latest_action.action == ModerationActionType.DELIST.value:
            return ModerationServiceState.DELISTED
        if latest_action.action == ModerationActionType.RESTORE.value:
            return ModerationServiceState.CLEAR

        msg = f"unknown moderation action stored: {latest_action.action}"
        raise RuntimeError(msg)

    async def ensure_service_available(self, service_id: int) -> None:
        state = await self.get_service_state(service_id)
        if state is ModerationServiceState.CLEAR:
            return

        raise ServiceUnavailableError(service_id=service_id, state=state)

    async def _record_action(
        self,
        *,
        service_id: int,
        reason: str,
        actor: ActorContext | None,
        action: ModerationActionType,
    ) -> ModerationActionRecord:
        state = await self.get_service_state(service_id)
        if not _is_valid_transition(state, action):
            raise InvalidModerationTransitionError(
                service_id=service_id,
                current_state=state,
                attempted_action=action,
            )

        record = self._moderation_action_repo.add(
            service_id=service_id,
            actor_account_id=None if actor is None else actor.account_id,
            action=action.value,
            reason=reason,
        )
        await self._session.commit()
        await self._session.refresh(record)
        return record


def _is_valid_transition(
    state: ModerationServiceState,
    action: ModerationActionType,
) -> bool:
    allowed_actions = {
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
        },
    }
    return action in allowed_actions[state]
