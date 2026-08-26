from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_record,
    create_moderation_action_record,
    create_provider_account_record,
    create_service_record,
    create_upstream_record,
)

from app.core.enums import ServiceLifecycle
from app.core.errors import NotFoundError
from app.db.models import Service
from app.schemas.discovery import PublicServiceRef
from app.services import discovery

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]


async def test_list_services_returns_only_active_services_with_enabled_endpoints(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    listed_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="listed-service",
    )
    await create_endpoint_record(db_session_factory, service_id=listed_service_id)
    draft_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="draft-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    await create_endpoint_record(db_session_factory, service_id=draft_service_id)
    disabled_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="disabled-endpoints-service",
    )
    await create_endpoint_record(
        db_session_factory,
        service_id=disabled_service_id,
        is_enabled=False,
    )

    async with db_session_factory() as session:
        services = await discovery.list_services(session=session)

    assert [service.slug for service in services] == ["listed-service"]


async def test_list_services_hides_moderated_services_until_restored(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    suspended_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="suspended-service",
    )
    await create_endpoint_record(db_session_factory, service_id=suspended_service_id)
    delisted_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="delisted-service",
    )
    await create_endpoint_record(db_session_factory, service_id=delisted_service_id)
    await create_moderation_action_record(
        db_session_factory,
        service_id=suspended_service_id,
        action="suspend",
    )
    await create_moderation_action_record(
        db_session_factory,
        service_id=delisted_service_id,
        action="delist",
    )

    async with db_session_factory() as session:
        moderated_services = await discovery.list_services(session=session)

    await create_moderation_action_record(
        db_session_factory,
        service_id=suspended_service_id,
        action="restore",
    )

    async with db_session_factory() as session:
        restored_services = await discovery.list_services(session=session)

    assert moderated_services == []
    assert [service.slug for service in restored_services] == ["suspended-service"]


async def test_list_services_orders_by_creation_time_then_id(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    older_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="older-service",
    )
    await create_endpoint_record(db_session_factory, service_id=older_service_id)
    first_tied_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="first-tied-service",
    )
    await create_endpoint_record(db_session_factory, service_id=first_tied_service_id)
    second_tied_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="second-tied-service",
    )
    await create_endpoint_record(db_session_factory, service_id=second_tied_service_id)

    # The two newest services share a creation timestamp so the id tiebreak is what orders them.
    async with db_session_factory.begin() as session:
        await session.execute(
            update(Service)
            .where(Service.id == older_service_id)
            .values(created_at=datetime(2026, 1, 1, tzinfo=UTC)),
        )
        await session.execute(
            update(Service)
            .where(Service.id.in_([first_tied_service_id, second_tied_service_id]))
            .values(created_at=datetime(2026, 2, 1, tzinfo=UTC)),
        )

    async with db_session_factory() as session:
        services = await discovery.list_services(session=session)

    assert [service.id for service in services] == [
        second_tied_service_id,
        first_tied_service_id,
        older_service_id,
    ]


async def test_get_service_resolves_by_id_and_by_slug(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="resolvable-service",
    )
    await create_endpoint_record(db_session_factory, service_id=service_id)

    async with db_session_factory() as session:
        by_id = await discovery.get_service(
            session=session,
            service_ref=PublicServiceRef(id=service_id),
        )
        by_slug = await discovery.get_service(
            session=session,
            service_ref=PublicServiceRef(slug="resolvable-service"),
        )

    assert by_id.id == service_id
    assert by_slug.id == service_id


async def test_get_service_rejects_hidden_services(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    draft_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="hidden-draft-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    await create_endpoint_record(db_session_factory, service_id=draft_service_id)
    disabled_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="hidden-disabled-service",
    )
    await create_endpoint_record(
        db_session_factory,
        service_id=disabled_service_id,
        is_enabled=False,
    )
    suspended_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="hidden-suspended-service",
    )
    await create_endpoint_record(db_session_factory, service_id=suspended_service_id)
    await create_moderation_action_record(
        db_session_factory,
        service_id=suspended_service_id,
        action="suspend",
    )

    async with db_session_factory() as session:
        for hidden_service_id in (draft_service_id, disabled_service_id, suspended_service_id):
            with pytest.raises(NotFoundError):
                await discovery.get_service(
                    session=session,
                    service_ref=PublicServiceRef(id=hidden_service_id),
                )
        with pytest.raises(NotFoundError):
            await discovery.get_service(
                session=session,
                service_ref=PublicServiceRef(slug="never-created-service"),
            )


async def test_discovery_reads_load_pricing_but_not_upstream(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="upstream-service",
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)
    await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        listed_service = (await discovery.list_services(session=session))[0]
        listed_unloaded = inspect(listed_service.endpoints[0]).unloaded

    async with db_session_factory() as session:
        detail_service = await discovery.get_service(
            session=session,
            service_ref=PublicServiceRef(id=service_id),
        )
        detail_unloaded = inspect(detail_service.endpoints[0]).unloaded

    assert "upstream" in listed_unloaded
    assert "price" not in listed_unloaded
    assert "upstream" in detail_unloaded
    assert "price" not in detail_unloaded
