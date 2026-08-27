import asyncio

import pytest
from pydantic import HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from tests.fixtures.domain import (
    create_admin_account_record,
    create_endpoint_price_record,
    create_endpoint_record,
    create_provider_account_record,
    create_service_record,
    create_upstream_record,
)

from app.core.config import Settings
from app.core.enums import AccessMode, AppEnv, ServiceLifecycle
from app.core.errors import InvalidStateError
from app.db.models import ModerationAction, Service, ServiceEndpoint, ServiceRevision
from app.schemas.service import EndpointUpdateRequest, EndpointUpstreamRequest
from app.services import moderation, provider_endpoints, publishing, revisions, service_access

# Bounded wait proving the concurrent mutation is still blocked on publish's
# row lock rather than racing ahead of publish's single commit.
LOCK_WAIT_TIMEOUT_SECONDS = 0.5


def _upstream_settings() -> Settings:
    return Settings(env=AppEnv.TEST, jwt_secret_key="test-secret-key-with-32-bytes-123")


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    return await create_provider_account_record(
        db_session_factory,
        display_name="Provider",
    )


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str,
    lifecycle: ServiceLifecycle,
) -> int:
    return await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        lifecycle=lifecycle,
    )


async def _seed_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    key: str = "translate",
    access_mode: AccessMode = AccessMode.FREE,
) -> int:
    return await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key=key,
        access_mode=access_mode,
    )


async def _seed_fixed_price(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    amount_minor: int = 1500,
    currency: str = "USD",
) -> None:
    await create_endpoint_price_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=amount_minor,
        currency=currency,
    )


async def _load_service_graph(
    session: AsyncSession,
    *,
    service_id: int,
) -> Service | None:
    return await session.scalar(
        select(Service)
        .options(
            selectinload(Service.endpoints).selectinload(ServiceEndpoint.upstream),
        )
        .where(Service.id == service_id),
    )


async def _seed_upstream(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    path: str = "/invoke",
) -> None:
    await create_upstream_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        path=path,
    )


@pytest.mark.asyncio
async def test_concurrent_active_endpoint_updates_create_distinct_revisions(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = migrated_database
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="concurrent-active-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
    )

    original_create_revision = revisions.create_revision
    first_revision_created = asyncio.Event()
    release_first_revision = asyncio.Event()
    create_revision_calls = 0

    async def delayed_create_revision(
        *,
        session: AsyncSession,
        service: Service,
    ) -> ServiceRevision:
        nonlocal create_revision_calls
        revision = await original_create_revision(session=session, service=service)
        create_revision_calls += 1
        if create_revision_calls == 1:
            first_revision_created.set()
            await release_first_revision.wait()
        return revision

    monkeypatch.setattr(revisions, "create_revision", delayed_create_revision)

    async def update_timeout(timeout_seconds: int) -> None:
        async with db_session_factory() as session:
            await provider_endpoints.update_endpoint(
                session=session,
                account_id=provider_account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(timeout_seconds=timeout_seconds),
            )

    first_update = asyncio.create_task(update_timeout(45))
    await first_revision_created.wait()
    second_update = asyncio.create_task(update_timeout(60))
    release_first_revision.set()
    await asyncio.gather(first_update, second_update)

    async with db_session_factory() as session:
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )
        persisted_revisions = list(
            (
                await session.scalars(
                    select(ServiceRevision)
                    .where(ServiceRevision.service_id == service_id)
                    .order_by(
                        ServiceRevision.revision_number.desc(),
                        ServiceRevision.id.desc(),
                    ),
                )
            ).all()
        )

    assert revision_count == 2
    assert [revision.revision_number for revision in persisted_revisions] == [2, 1]


@pytest.mark.asyncio
async def test_publish_rejects_concurrent_draft_upstream_mutation_it_beat_to_the_lock(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = migrated_database
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="publish-race-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.FREE,
    )
    await _seed_upstream(db_session_factory, endpoint_id=endpoint_id)

    publish_has_lock = asyncio.Event()
    mutation_lookup_started = asyncio.Event()
    release_publish = asyncio.Event()
    original_load_owned_service_for_update = service_access.load_owned_service_for_update
    original_lock_owned_service_by_endpoint = service_access.lock_owned_service_by_endpoint

    async def delayed_load_owned_service_for_update(
        *,
        session: AsyncSession,
        account_id: int,
        service_id: int,
    ) -> Service:
        service = await original_load_owned_service_for_update(
            session=session,
            account_id=account_id,
            service_id=service_id,
        )
        publish_has_lock.set()
        await release_publish.wait()
        return service

    async def signalling_lock_owned_service_by_endpoint(
        *,
        session: AsyncSession,
        account_id: int,
        endpoint_id: int,
    ) -> int:
        mutation_lookup_started.set()
        return await original_lock_owned_service_by_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
        )

    monkeypatch.setattr(
        service_access,
        "load_owned_service_for_update",
        delayed_load_owned_service_for_update,
    )
    monkeypatch.setattr(
        service_access,
        "lock_owned_service_by_endpoint",
        signalling_lock_owned_service_by_endpoint,
    )

    async def publish() -> None:
        async with db_session_factory() as session:
            await publishing.publish_service(
                session=session,
                account_id=provider_account_id,
                service_id=service_id,
            )

    async def mutate_upstream() -> None:
        await publish_has_lock.wait()
        async with db_session_factory() as session:
            # Publish holds the service row lock, so this waits for publish's
            # single commit and then finds an already-active service.
            with pytest.raises(InvalidStateError, match="service is not mutable outside draft"):
                await provider_endpoints.upsert_upstream(
                    session=session,
                    settings=_upstream_settings(),
                    account_id=provider_account_id,
                    endpoint_id=endpoint_id,
                    request=EndpointUpstreamRequest(
                        base_url=HttpUrl("http://127.0.0.1:9000"),
                        path="/mutated",
                        http_method="POST",
                    ),
                )

    publish_task = asyncio.create_task(publish())
    await publish_has_lock.wait()
    mutate_task = asyncio.create_task(mutate_upstream())
    await mutation_lookup_started.wait()
    release_publish.set()
    await publish_task
    await mutate_task

    async with db_session_factory() as session:
        service = await _load_service_graph(session, service_id=service_id)

    assert service is not None
    assert service.lifecycle is ServiceLifecycle.ACTIVE
    assert service.endpoints[0].upstream is not None
    assert service.endpoints[0].upstream.path == "/invoke"


@pytest.mark.asyncio
async def test_publish_holds_its_lock_until_the_single_commit(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = migrated_database
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="publish-single-transaction-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await _seed_endpoint(
        db_session_factory,
        service_id=service_id,
        access_mode=AccessMode.FREE,
    )
    await _seed_upstream(db_session_factory, endpoint_id=endpoint_id)

    # Publish pauses between recording its readiness verdict and flipping the
    # lifecycle - the window in which it used to have already committed and
    # released the service row lock.
    original_create_revision = revisions.create_revision
    publish_reached_revision = asyncio.Event()
    release_publish = asyncio.Event()
    original_lock_owned_service_by_endpoint = service_access.lock_owned_service_by_endpoint
    mutation_lock_started = asyncio.Event()

    async def delayed_create_revision(
        *,
        session: AsyncSession,
        service: Service,
    ) -> ServiceRevision:
        publish_reached_revision.set()
        await release_publish.wait()
        return await original_create_revision(session=session, service=service)

    async def signalling_lock_owned_service_by_endpoint(
        *,
        session: AsyncSession,
        account_id: int,
        endpoint_id: int,
    ) -> int:
        mutation_lock_started.set()
        return await original_lock_owned_service_by_endpoint(
            session=session,
            account_id=account_id,
            endpoint_id=endpoint_id,
        )

    monkeypatch.setattr(revisions, "create_revision", delayed_create_revision)
    monkeypatch.setattr(
        service_access,
        "lock_owned_service_by_endpoint",
        signalling_lock_owned_service_by_endpoint,
    )

    async def publish() -> None:
        async with db_session_factory() as session:
            await publishing.publish_service(
                session=session,
                account_id=provider_account_id,
                service_id=service_id,
            )

    async def mutate_timeout() -> None:
        async with db_session_factory() as session:
            await provider_endpoints.update_endpoint(
                session=session,
                account_id=provider_account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(timeout_seconds=45),
            )

    publish_task = asyncio.create_task(publish())
    await publish_reached_revision.wait()
    mutate_task = asyncio.create_task(mutate_timeout())
    await mutation_lock_started.wait()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(mutate_task), timeout=LOCK_WAIT_TIMEOUT_SECONDS)

    assert not mutate_task.done()
    assert not publish_task.done()

    release_publish.set()
    await publish_task
    await mutate_task

    async with db_session_factory() as session:
        service = await _load_service_graph(session, service_id=service_id)

    assert service is not None
    assert service.lifecycle is ServiceLifecycle.ACTIVE
    assert service.current_revision_id is not None
    assert service.current_change_token is not None
    assert service.endpoints[0].timeout_seconds == 45


@pytest.mark.asyncio
async def test_concurrent_suspends_serialise_on_the_service_row_lock(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = migrated_database
    provider_account_id = await _create_provider_account(db_session_factory)
    admin_account_id = await create_admin_account_record(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="suspend-race-service",
        lifecycle=ServiceLifecycle.ACTIVE,
    )

    # The first suspend pauses after reading the moderation state, holding the
    # service row lock across the window the second suspend has to survive.
    original_get_service_state = moderation.get_service_state
    first_suspend_has_lock = asyncio.Event()
    release_first_suspend = asyncio.Event()
    state_reads = 0

    async def delayed_get_service_state(
        *,
        session: AsyncSession,
        service_id: int,
    ) -> moderation.ModerationServiceState:
        nonlocal state_reads
        state = await original_get_service_state(session=session, service_id=service_id)
        state_reads += 1
        if state_reads == 1:
            first_suspend_has_lock.set()
            await release_first_suspend.wait()
        return state

    monkeypatch.setattr(moderation, "get_service_state", delayed_get_service_state)

    async def suspend(reason: str) -> None:
        async with db_session_factory() as session:
            await moderation.suspend_service(
                session=session,
                service_id=service_id,
                actor_account_id=admin_account_id,
                reason=reason,
            )

    async def losing_suspend() -> None:
        async with db_session_factory() as session:
            with pytest.raises(
                InvalidStateError,
                match=f"cannot suspend service {service_id} from suspended",
            ):
                await moderation.suspend_service(
                    session=session,
                    service_id=service_id,
                    actor_account_id=admin_account_id,
                    reason="second",
                )

    first_task = asyncio.create_task(suspend("first"))
    await first_suspend_has_lock.wait()
    second_task = asyncio.create_task(losing_suspend())

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(second_task), timeout=LOCK_WAIT_TIMEOUT_SECONDS)

    assert not second_task.done()

    release_first_suspend.set()
    await first_task
    await second_task

    async with db_session_factory() as session:
        action_count = await session.scalar(
            select(func.count())
            .select_from(ModerationAction)
            .where(ModerationAction.service_id == service_id),
        )
        persisted_actions = list(
            (
                await session.scalars(
                    select(ModerationAction).where(ModerationAction.service_id == service_id),
                )
            ).all()
        )

    assert action_count == 1
    assert persisted_actions[0].action == moderation.ModerationActionType.SUSPEND.value
    assert persisted_actions[0].reason == "first"
