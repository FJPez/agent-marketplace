from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from app.core.actor import ActorContext
from app.services.moderation_service import (
    InvalidModerationTransitionError,
    ModerationActionType,
    ModerationService,
    ModerationServiceState,
    ServiceUnavailableError,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.refreshed: list[object] = []

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, instance: object) -> None:
        self.refreshed.append(instance)


@dataclass(slots=True)
class FakeModerationActionRecord:
    service_id: int
    actor_account_id: int | None
    action: str
    reason: str
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 3, 12, tzinfo=UTC),
    )


class FakeModerationActionRepository:
    def __init__(
        self,
        history: list[FakeModerationActionRecord] | None = None,
    ) -> None:
        self._history = history or []

    async def get_latest_for_service(
        self,
        service_id: int,
    ) -> FakeModerationActionRecord | None:
        for record in reversed(self._history):
            if record.service_id == service_id:
                return record
        return None

    def add(
        self,
        *,
        service_id: int,
        actor_account_id: int | None,
        action: str,
        reason: str,
    ) -> FakeModerationActionRecord:
        record = FakeModerationActionRecord(
            service_id=service_id,
            actor_account_id=actor_account_id,
            action=action,
            reason=reason,
        )
        self._history.append(record)
        return record


def _service(
    *,
    history: list[FakeModerationActionRecord] | None = None,
    session: FakeSession | None = None,
) -> tuple[ModerationService, FakeSession]:
    resolved_session = session or FakeSession()
    service = ModerationService(
        cast("AsyncSession", resolved_session),
        moderation_action_repo=FakeModerationActionRepository(history),
    )
    return service, resolved_session


@pytest.mark.asyncio
async def test_suspend_service_records_action_and_marks_service_suspended() -> None:
    service, session = _service()

    record = await service.suspend_service(
        service_id=42,
        reason="spam",
        actor=ActorContext(account_id=7),
    )

    assert record.service_id == 42
    assert record.actor_account_id == 7
    assert record.action == ModerationActionType.SUSPEND.value
    assert record.reason == "spam"
    assert await service.get_service_state(42) is ModerationServiceState.SUSPENDED
    assert session.commits == 1
    assert session.refreshed == [record]


@pytest.mark.asyncio
async def test_delist_service_records_action_and_marks_service_delisted() -> None:
    service, session = _service()

    record = await service.delist_service(
        service_id=42,
        reason="policy violation",
        actor=None,
    )

    assert record.service_id == 42
    assert record.actor_account_id is None
    assert record.action == ModerationActionType.DELIST.value
    assert await service.get_service_state(42) is ModerationServiceState.DELISTED
    assert session.commits == 1
    assert session.refreshed == [record]


@pytest.mark.asyncio
async def test_restore_service_clears_suspended_service() -> None:
    history = [
        FakeModerationActionRecord(
            service_id=42,
            actor_account_id=7,
            action=ModerationActionType.SUSPEND.value,
            reason="spam",
        ),
    ]
    service, session = _service(history=history)

    record = await service.restore_service(
        service_id=42,
        reason="remediated",
        actor=ActorContext(account_id=9),
    )

    assert record.action == ModerationActionType.RESTORE.value
    assert record.actor_account_id == 9
    assert await service.get_service_state(42) is ModerationServiceState.CLEAR
    assert session.commits == 1


@pytest.mark.asyncio
async def test_restore_service_clears_delisted_service() -> None:
    history = [
        FakeModerationActionRecord(
            service_id=42,
            actor_account_id=None,
            action=ModerationActionType.DELIST.value,
            reason="policy violation",
        ),
    ]
    service, _ = _service(history=history)

    await service.restore_service(
        service_id=42,
        reason="approved for relisting",
        actor=None,
    )

    assert await service.get_service_state(42) is ModerationServiceState.CLEAR


@pytest.mark.asyncio
async def test_delist_service_from_suspended_marks_service_delisted() -> None:
    history = [
        FakeModerationActionRecord(
            service_id=42,
            actor_account_id=7,
            action=ModerationActionType.SUSPEND.value,
            reason="spam",
        ),
    ]
    service, _ = _service(history=history)

    await service.delist_service(
        service_id=42,
        reason="repeat violation",
        actor=None,
    )

    assert await service.get_service_state(42) is ModerationServiceState.DELISTED


@pytest.mark.asyncio
async def test_restore_service_rejects_clear_service() -> None:
    service, _ = _service()

    with pytest.raises(InvalidModerationTransitionError) as exc_info:
        await service.restore_service(
            service_id=42,
            reason="not needed",
            actor=None,
        )

    assert exc_info.value.service_id == 42
    assert exc_info.value.current_state is ModerationServiceState.CLEAR
    assert exc_info.value.attempted_action is ModerationActionType.RESTORE


@pytest.mark.asyncio
async def test_suspend_service_rejects_already_suspended_service() -> None:
    history = [
        FakeModerationActionRecord(
            service_id=42,
            actor_account_id=7,
            action=ModerationActionType.SUSPEND.value,
            reason="spam",
        ),
    ]
    service, session = _service(history=history)

    with pytest.raises(InvalidModerationTransitionError) as exc_info:
        await service.suspend_service(
            service_id=42,
            reason="repeat spam",
            actor=None,
        )

    assert exc_info.value.current_state is ModerationServiceState.SUSPENDED
    assert session.commits == 0


@pytest.mark.asyncio
async def test_delist_service_rejects_already_delisted_service() -> None:
    history = [
        FakeModerationActionRecord(
            service_id=42,
            actor_account_id=None,
            action=ModerationActionType.DELIST.value,
            reason="policy violation",
        ),
    ]
    service, session = _service(history=history)

    with pytest.raises(InvalidModerationTransitionError) as exc_info:
        await service.delist_service(
            service_id=42,
            reason="still delisted",
            actor=None,
        )

    assert exc_info.value.current_state is ModerationServiceState.DELISTED
    assert session.commits == 0


@pytest.mark.asyncio
async def test_suspend_service_rejects_delisted_service() -> None:
    history = [
        FakeModerationActionRecord(
            service_id=42,
            actor_account_id=None,
            action=ModerationActionType.DELIST.value,
            reason="policy violation",
        ),
    ]
    service, _ = _service(history=history)

    with pytest.raises(InvalidModerationTransitionError) as exc_info:
        await service.suspend_service(
            service_id=42,
            reason="secondary action",
            actor=None,
        )

    assert exc_info.value.current_state is ModerationServiceState.DELISTED
    assert exc_info.value.attempted_action is ModerationActionType.SUSPEND


@pytest.mark.asyncio
async def test_ensure_service_available_blocks_suspended_service() -> None:
    history = [
        FakeModerationActionRecord(
            service_id=42,
            actor_account_id=7,
            action=ModerationActionType.SUSPEND.value,
            reason="spam",
        ),
    ]
    service, _ = _service(history=history)

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await service.ensure_service_available(42)

    assert exc_info.value.service_id == 42
    assert exc_info.value.state is ModerationServiceState.SUSPENDED


@pytest.mark.asyncio
async def test_ensure_service_available_blocks_delisted_service() -> None:
    history = [
        FakeModerationActionRecord(
            service_id=42,
            actor_account_id=None,
            action=ModerationActionType.DELIST.value,
            reason="policy violation",
        ),
    ]
    service, _ = _service(history=history)

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await service.ensure_service_available(42)

    assert exc_info.value.service_id == 42
    assert exc_info.value.state is ModerationServiceState.DELISTED


@pytest.mark.asyncio
async def test_ensure_service_available_allows_clear_service() -> None:
    service, _ = _service()

    await service.ensure_service_available(42)
