import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_record,
    create_provider_account_record,
    create_service_record,
    create_upstream_record,
)

from app.core.enums import AccessMode, ServiceHealthStatus, ServiceLifecycle
from app.core.errors import InvalidInputError, InvalidStateError, NotFoundError
from app.db.models import Service, ServiceHealthCheck, ServiceRevision
from app.services import publishing

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]


async def _seed_publishable_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str,
    access_mode: AccessMode = AccessMode.FREE,
    with_upstream: bool = True,
) -> int:
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=access_mode,
    )
    if with_upstream:
        await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)
    return service_id


async def _health_checks(
    session: AsyncSession,
    *,
    service_id: int,
) -> list[ServiceHealthCheck]:
    result = await session.scalars(
        select(ServiceHealthCheck)
        .where(ServiceHealthCheck.service_id == service_id)
        .order_by(ServiceHealthCheck.checked_at.desc(), ServiceHealthCheck.id.desc()),
    )
    return list(result.all())


async def test_publish_service_activates_service_with_revision_and_pass_check(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="publishable-service",
    )

    async with db_session_factory() as session:
        published = await publishing.publish_service(
            session=session,
            account_id=provider_account_id,
            service_id=service_id,
        )

    assert published.lifecycle is ServiceLifecycle.ACTIVE

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)
        revision = await session.scalar(
            select(ServiceRevision).where(ServiceRevision.service_id == service_id),
        )
        checks = await _health_checks(session, service_id=service_id)

    assert service is not None
    assert service.lifecycle is ServiceLifecycle.ACTIVE
    assert revision is not None
    assert revision.revision_number == 1
    assert service.current_revision_id == revision.id
    assert service.current_change_token == revision.change_token
    assert [check.status for check in checks] == [ServiceHealthStatus.PASS]
    assert checks[0].summary == "service is publish-ready"
    assert checks[0].details == {"enabled_endpoint_count": 1}


async def test_publish_service_rejects_second_publish_of_active_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="twice-published-service",
    )

    async with db_session_factory() as session:
        await publishing.publish_service(
            session=session,
            account_id=provider_account_id,
            service_id=service_id,
        )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="service is not publishable outside draft"):
            await publishing.publish_service(
                session=session,
                account_id=provider_account_id,
                service_id=service_id,
            )


async def test_publish_service_rejects_unknown_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="service not found"):
            await publishing.publish_service(
                session=session,
                account_id=provider_account_id,
                service_id=987654,
            )


async def test_publish_service_persists_fail_check_and_leaves_service_in_draft(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="unready-service",
        with_upstream=False,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match="must define upstream before publish"):
            await publishing.publish_service(
                session=session,
                account_id=provider_account_id,
                service_id=service_id,
            )

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(ServiceRevision)
            .where(ServiceRevision.service_id == service_id),
        )
        checks = await _health_checks(session, service_id=service_id)

    assert service is not None
    assert service.lifecycle is ServiceLifecycle.DRAFT
    assert service.current_revision_id is None
    assert service.current_change_token is None
    assert revision_count == 0
    assert [check.status for check in checks] == [ServiceHealthStatus.FAIL]
    assert checks[0].summary == "enabled endpoint 'translate' must define upstream before publish"


async def test_publish_service_adds_pass_check_beside_earlier_failed_attempt(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="repaired-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError):
            await publishing.publish_service(
                session=session,
                account_id=provider_account_id,
                service_id=service_id,
            )

    await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        await publishing.publish_service(
            session=session,
            account_id=provider_account_id,
            service_id=service_id,
        )

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)
        checks = await _health_checks(session, service_id=service_id)

    assert service is not None
    assert service.lifecycle is ServiceLifecycle.ACTIVE
    assert [check.status for check in checks] == [
        ServiceHealthStatus.PASS,
        ServiceHealthStatus.FAIL,
    ]
