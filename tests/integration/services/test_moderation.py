from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_admin_account_record,
    create_moderation_action_record,
    create_provider_account_record,
    create_service_record,
)

from app.core.errors import InvalidStateError, NotFoundError
from app.db.models import ModerationAction, Service
from app.services import moderation
from app.services.moderation import ModerationServiceState, ServiceUnavailableError

pytestmark = [pytest.mark.asyncio]

type ModerationMutation = Callable[..., Awaitable[ModerationAction]]

MISSING_SERVICE_ID = 999_999

ALLOWED_TRANSITIONS = [
    ([], moderation.suspend_service, "suspend"),
    ([], moderation.delist_service, "delist"),
    (["suspend"], moderation.restore_service, "restore"),
    (["suspend"], moderation.delist_service, "delist"),
    (["delist"], moderation.restore_service, "restore"),
    (["delist"], moderation.suspend_service, "suspend"),
]

REJECTED_TRANSITIONS = [
    ([], moderation.restore_service, "cannot restore service {service_id} from clear"),
    (["suspend"], moderation.suspend_service, "cannot suspend service {service_id} from suspended"),
    (["delist"], moderation.delist_service, "cannot delist service {service_id} from delisted"),
]

DERIVED_STATES = [
    ([], ModerationServiceState.CLEAR),
    (["suspend"], ModerationServiceState.SUSPENDED),
    (["delist"], ModerationServiceState.DELISTED),
    (["suspend", "restore"], ModerationServiceState.CLEAR),
    (["delist", "restore"], ModerationServiceState.CLEAR),
    (["suspend", "delist"], ModerationServiceState.DELISTED),
    (["delist", "restore", "suspend"], ModerationServiceState.SUSPENDED),
]


@pytest.mark.parametrize(("history", "mutate", "expected_action"), ALLOWED_TRANSITIONS)
async def test_allowed_transition_records_the_action(
    db_session_factory: async_sessionmaker[AsyncSession],
    history: list[str],
    mutate: ModerationMutation,
    expected_action: str,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    admin_account_id = await create_admin_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="transition-target",
    )
    for seeded_action in history:
        await create_moderation_action_record(
            db_session_factory,
            service_id=service_id,
            action=seeded_action,
        )
    requested_at = datetime.now(UTC)

    async with db_session_factory() as session:
        recorded = await mutate(
            session=session,
            service_id=service_id,
            actor_account_id=admin_account_id,
            reason="policy call",
        )

    async with db_session_factory() as session:
        persisted = await session.get(ModerationAction, recorded.id)

    assert persisted is not None
    assert persisted.service_id == service_id
    assert persisted.actor_account_id == admin_account_id
    assert persisted.action == expected_action
    assert persisted.reason == "policy call"
    assert persisted.created_at >= requested_at


@pytest.mark.parametrize(("history", "mutate", "expected_message"), REJECTED_TRANSITIONS)
async def test_rejected_transition_raises_and_records_nothing(
    db_session_factory: async_sessionmaker[AsyncSession],
    history: list[str],
    mutate: ModerationMutation,
    expected_message: str,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    admin_account_id = await create_admin_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="rejected-target",
    )
    for seeded_action in history:
        await create_moderation_action_record(
            db_session_factory,
            service_id=service_id,
            action=seeded_action,
        )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError) as error:
            await mutate(
                session=session,
                service_id=service_id,
                actor_account_id=admin_account_id,
                reason="policy call",
            )

    async with db_session_factory() as session:
        recorded_actions = await session.scalar(
            select(func.count()).select_from(ModerationAction),
        )

    assert str(error.value) == expected_message.format(service_id=service_id)
    assert recorded_actions == len(history)


async def test_suspending_a_missing_service_raises_not_found_and_records_nothing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_account_id = await create_admin_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError) as error:
            await moderation.suspend_service(
                session=session,
                service_id=MISSING_SERVICE_ID,
                actor_account_id=admin_account_id,
                reason="policy call",
            )

    async with db_session_factory() as session:
        recorded_actions = await session.scalar(
            select(func.count()).select_from(ModerationAction),
        )

    assert str(error.value) == "service not found"
    assert recorded_actions == 0


@pytest.mark.parametrize(("history", "expected_state"), DERIVED_STATES)
async def test_the_moderation_state_follows_the_latest_action(
    db_session_factory: async_sessionmaker[AsyncSession],
    history: list[str],
    expected_state: ModerationServiceState,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="state-target",
    )
    for seeded_action in history:
        await create_moderation_action_record(
            db_session_factory,
            service_id=service_id,
            action=seeded_action,
        )

    async with db_session_factory() as session:
        state = await moderation.get_service_state(session=session, service_id=service_id)
        is_clear = await session.scalar(
            select(moderation.is_clear()).where(Service.id == service_id),
        )

    assert state is expected_state
    assert is_clear is (expected_state is ModerationServiceState.CLEAR)


async def test_a_delisted_service_may_still_publish(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="delisted-target",
    )
    await create_moderation_action_record(
        db_session_factory,
        service_id=service_id,
        action="delist",
    )

    async with db_session_factory() as session:
        await moderation.ensure_service_publishable(session=session, service_id=service_id)


async def test_a_suspended_service_may_not_publish(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="suspended-target",
    )
    await create_moderation_action_record(
        db_session_factory,
        service_id=service_id,
        action="suspend",
    )

    async with db_session_factory() as session:
        with pytest.raises(ServiceUnavailableError) as error:
            await moderation.ensure_service_publishable(session=session, service_id=service_id)

    assert error.value.service_id == service_id
    assert error.value.state is ModerationServiceState.SUSPENDED


async def test_list_actions_returns_the_service_history_oldest_first(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="history-target",
    )
    other_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="other-target",
    )
    for seeded_action in ("suspend", "restore", "delist"):
        await create_moderation_action_record(
            db_session_factory,
            service_id=service_id,
            action=seeded_action,
        )
    await create_moderation_action_record(
        db_session_factory,
        service_id=other_service_id,
        action="suspend",
    )

    async with db_session_factory() as session:
        actions = await moderation.list_actions(session=session, service_id=service_id)

    assert [action.action for action in actions] == ["suspend", "restore", "delist"]
    assert {action.service_id for action in actions} == {service_id}
