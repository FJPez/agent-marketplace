from ipaddress import IPv4Address

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_record,
    create_provider_account_record,
    create_service_record,
    create_trusted_provider_records,
    create_upstream_record,
)
from tests.helpers.dns import (
    TEST_DOMAIN_RECORD_NAME,
    TEST_UPSTREAM_ADDRESS,
    TEST_UPSTREAM_HOST,
    FakeResolver,
    TransactionWatchingResolver,
)

from app.core.enums import ServiceHealthStatus, ServiceLifecycle
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.db.models import ProviderUpstream, Service, ServiceHealthCheck, ServiceRevision
from app.services.domain_control import RECORD_LABEL, get_verification_record
from app.services.publishing import publish_service

pytestmark = [pytest.mark.asyncio]

OTHER_HOST = "api.other-provider.example"


async def _seed_publishable_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str,
    with_upstream: bool = True,
    trusted: bool = True,
) -> int:
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)
    if with_upstream:
        await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)
    if trusted:
        await create_trusted_provider_records(db_session_factory, account_id=provider_account_id)
    return service_id


async def _publish(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    resolver: FakeResolver,
    account_id: int,
    service_id: int,
) -> Service:
    async with db_session_factory() as session:
        return await publish_service(
            session=session,
            resolver=resolver,
            account_id=account_id,
            service_id=service_id,
        )


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


class RepointingResolver(FakeResolver):
    """Answers as `answers` does, but repoints an upstream at another host on its first lookup."""

    def __init__(
        self,
        answers: FakeResolver,
        db_session_factory: async_sessionmaker[AsyncSession],
        *,
        endpoint_id: int,
    ) -> None:
        super().__init__(answers.addresses, txt_records=answers.txt_records)
        self._db_session_factory = db_session_factory
        self._endpoint_id = endpoint_id

    async def resolve_ipv4(self, host: str) -> list[IPv4Address]:
        async with self._db_session_factory.begin() as session:
            await session.execute(
                update(ProviderUpstream)
                .where(ProviderUpstream.endpoint_id == self._endpoint_id)
                .values(base_url=f"https://{OTHER_HOST}/"),
            )
        return await super().resolve_ipv4(host)


async def test_publish_service_activates_service_with_revision_and_pass_check(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="publishable-service",
    )

    published = await _publish(
        db_session_factory,
        resolver=dns_resolver,
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
    assert checks[0].checked_at == service.updated_at


async def test_publish_service_rejects_second_publish_of_active_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="twice-published-service",
    )
    await _publish(
        db_session_factory,
        resolver=dns_resolver,
        account_id=provider_account_id,
        service_id=service_id,
    )

    with pytest.raises(InvalidStateError, match="service is not publishable outside draft"):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )


async def test_publish_service_rejects_unknown_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)

    with pytest.raises(NotFoundError, match="service not found"):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=987654,
        )


async def test_publish_service_persists_fail_check_and_leaves_service_in_draft(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="unready-service",
        with_upstream=False,
    )

    async with db_session_factory() as session:
        seeded_service = await session.get(Service, service_id)
        assert seeded_service is not None
        seeded_updated_at = seeded_service.updated_at

    with pytest.raises(InvalidInputError, match="must define upstream before publish"):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
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
    assert service.updated_at == seeded_updated_at
    assert [check.status for check in checks] == [ServiceHealthStatus.FAIL]
    assert checks[0].summary == "enabled endpoint 'translate' must define upstream before publish"


async def test_publish_service_adds_pass_check_beside_earlier_failed_attempt(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    await create_trusted_provider_records(db_session_factory, account_id=provider_account_id)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="repaired-service",
        lifecycle=ServiceLifecycle.DRAFT,
    )
    endpoint_id = await create_endpoint_record(db_session_factory, service_id=service_id)

    with pytest.raises(InvalidInputError):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)
    await _publish(
        db_session_factory,
        resolver=dns_resolver,
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


async def test_publish_without_a_signing_secret_fails_and_stays_draft(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="secretless-service",
        trusted=False,
    )
    # The provider proves its domain with its own record, but has no signing secret.
    async with db_session_factory() as session:
        record = await get_verification_record(
            session=session,
            account_id=provider_account_id,
        )
    resolver = FakeResolver(
        {TEST_UPSTREAM_HOST: [TEST_UPSTREAM_ADDRESS]},
        txt_records={TEST_DOMAIN_RECORD_NAME: [record.value]},
    )

    with pytest.raises(InvalidInputError, match="provider must issue a signing secret"):
        await _publish(
            db_session_factory,
            resolver=resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)
    assert service is not None
    assert service.lifecycle is ServiceLifecycle.DRAFT


async def test_publish_fails_when_a_disabled_endpoints_host_is_unproven(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="half-proven-service",
    )
    disabled_endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="later",
        is_enabled=False,
    )
    await create_upstream_record(
        db_session_factory,
        endpoint_id=disabled_endpoint_id,
        base_url=f"https://{OTHER_HOST}/",
    )
    dns_resolver.addresses[OTHER_HOST] = [TEST_UPSTREAM_ADDRESS]

    with pytest.raises(InvalidInputError):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)
        checks = await _health_checks(session, service_id=service_id)
    assert service is not None
    assert service.lifecycle is ServiceLifecycle.DRAFT
    assert [check.summary for check in checks] == [
        f"{OTHER_HOST} must carry the TXT record {RECORD_LABEL}.{OTHER_HOST}",
    ]


async def test_publish_holds_no_transaction_open_during_the_domain_check(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="watched-service",
    )

    async with db_session_factory() as session:
        watching = TransactionWatchingResolver(dns_resolver, session)
        await publish_service(
            session=session,
            resolver=watching,
            account_id=provider_account_id,
            service_id=service_id,
        )

    assert watching.in_transaction_during_lookups
    assert not any(watching.in_transaction_during_lookups)


async def test_publish_rejects_upstreams_changed_during_the_domain_check(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="moving-service",
    )
    async with db_session_factory() as session:
        endpoint_id = await session.scalar(
            select(ProviderUpstream.endpoint_id).limit(1),
        )
    assert endpoint_id is not None
    resolver = RepointingResolver(dns_resolver, db_session_factory, endpoint_id=endpoint_id)

    with pytest.raises(ConflictError, match="upstreams changed while publishing"):
        await _publish(
            db_session_factory,
            resolver=resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    async with db_session_factory() as session:
        service = await session.get(Service, service_id)
        checks = await _health_checks(session, service_id=service_id)
    assert service is not None
    assert service.lifecycle is ServiceLifecycle.DRAFT
    assert checks == []
