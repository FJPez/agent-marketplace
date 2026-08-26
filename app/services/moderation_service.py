from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.db.models import ModerationAction
from app.repositories.service_repo import ServiceRepository


class ModerationActionStore(Protocol):
    async def list_for_service(
        self,
        service_id: int,
    ) -> list[ModerationAction]: ...

    async def get_latest_for_service(
        self,
        service_id: int,
    ) -> ModerationAction | None: ...

    async def get_latest_for_services(
        self,
        service_ids: list[int],
    ) -> dict[int, ModerationAction]: ...

    def add(
        self,
        *,
        service_id: int,
        actor_account_id: int | None,
        action: str,
        reason: str,
    ) -> ModerationAction: ...


class ServiceLookupStore(Protocol):
    async def get_by_id(self, *, service_id: int) -> object | None: ...

    async def get_by_id_for_update(self, *, service_id: int) -> object | None: ...


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


class ModeratedServiceNotFoundError(Exception):
    pass


class ModerationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        moderation_action_repo: ModerationActionStore | None = None,
        service_repo: ServiceLookupStore | None = None,
    ) -> None:
        self._session = session
        resolved_service_repo: ServiceLookupStore = service_repo or ServiceRepository(session)
        if moderation_action_repo is None:
            from app.repositories.moderation_action_repo import ModerationActionRepository

            resolved_moderation_action_repo = ModerationActionRepository(session)
        else:
            resolved_moderation_action_repo = moderation_action_repo
        self._service_repo = resolved_service_repo
        self._moderation_action_repo = resolved_moderation_action_repo

    async def suspend_service(
        self,
        *,
        service_id: int,
        reason: str,
        actor: ActorContext | None,
    ) -> ModerationAction:
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
    ) -> ModerationAction:
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
    ) -> ModerationAction:
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

    async def ensure_service_publishable(self, service_id: int) -> None:
        state = await self.get_service_state(service_id)
        if state is ModerationServiceState.SUSPENDED:
            raise ServiceUnavailableError(service_id=service_id, state=state)

    async def list_actions(self, *, service_id: int) -> list[ModerationAction]:
        return await self._moderation_action_repo.list_for_service(service_id)

    async def get_unlisted_service_ids(self, service_ids: list[int]) -> set[int]:
        if not service_ids:
            return set()
        latest_actions = await self._moderation_action_repo.get_latest_for_services(service_ids)
        blocked: set[int] = set()
        for sid, action in latest_actions.items():
            if action.action != ModerationActionType.RESTORE.value:
                blocked.add(sid)
        return blocked

    async def _record_action(
        self,
        *,
        service_id: int,
        reason: str,
        actor: ActorContext | None,
        action: ModerationActionType,
    ) -> ModerationAction:
        service = await self._service_repo.get_by_id_for_update(service_id=service_id)
        if service is None:
            raise ModeratedServiceNotFoundError("service not found")
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
            ModerationActionType.SUSPEND,
        },
    }
    return action in allowed_actions[state]
