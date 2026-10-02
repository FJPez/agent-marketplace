from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_record,
    create_moderation_action_record,
    create_provider_account_record,
    create_service_record,
    create_signing_secret_record,
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
from app.db.models import (
    ProviderUpstream,
    Service,
    ServiceEndpoint,
    ServiceHealthCheck,
    ServiceRevision,
)
from app.services import publishing
from app.services.domain_control import record_value
from app.services.service_health import (
    DOMAIN_CONTROL_CHECK_NAME,
    PUBLISH_READINESS_CHECK_NAME,
)

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
        return await publishing.publish_service(
            session=session,
            resolver=resolver,
            account_id=account_id,
            service_id=service_id,
        )


async def _health_checks(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
) -> list[ServiceHealthCheck]:
    """The service's health checks, newest first."""
    async with db_session_factory() as session:
        result = await session.scalars(
            select(ServiceHealthCheck)
            .where(ServiceHealthCheck.service_id == service_id)
            .order_by(ServiceHealthCheck.checked_at.desc(), ServiceHealthCheck.id.desc()),
        )
        return list(result.all())


async def _lifecycle(
    db_session_factory: async_sessionmaker[AsyncSession],
    service_id: int,
) -> ServiceLifecycle:
    async with db_session_factory() as session:
        service = await session.get(Service, service_id)
    assert service is not None
    return service.lifecycle


async def test_publish_service_activates_service_with_revision_and_pass_checks(
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
    checks = await _health_checks(db_session_factory, service_id=service_id)

    assert service is not None
    assert service.lifecycle is ServiceLifecycle.ACTIVE
    assert revision is not None
    assert revision.revision_number == 1
    assert service.current_revision_id == revision.id
    assert service.current_change_token == revision.change_token
    assert [(check.check_name, check.status, check.summary, check.details) for check in checks] == [
        (
            DOMAIN_CONTROL_CHECK_NAME,
            ServiceHealthStatus.PASS,
            "the provider controls every upstream host",
            {"hosts": {TEST_UPSTREAM_HOST: "verified"}},
        ),
        (
            PUBLISH_READINESS_CHECK_NAME,
            ServiceHealthStatus.PASS,
            "service is publish-ready",
            {"enabled_endpoint_count": 1},
        ),
    ]
    assert {check.checked_at for check in checks} == {service.updated_at}


async def test_publish_service_rejects_second_publish_of_active_service_before_any_lookup(
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
    dns_resolver.lookups.clear()

    with pytest.raises(InvalidStateError, match="service is not publishable outside draft"):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    assert dns_resolver.lookups == []


async def test_publish_service_rejects_a_suspended_draft_before_any_lookup(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="suspended-draft-service",
    )
    await create_moderation_action_record(
        db_session_factory,
        service_id=service_id,
        action="suspend",
    )

    with pytest.raises(InvalidStateError, match="service is suspended"):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    assert dns_resolver.lookups == []
    assert await _health_checks(db_session_factory, service_id=service_id) == []


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
    checks = await _health_checks(db_session_factory, service_id=service_id)

    assert service is not None
    assert service.lifecycle is ServiceLifecycle.DRAFT
    assert service.current_revision_id is None
    assert service.current_change_token is None
    assert revision_count == 0
    assert service.updated_at == seeded_updated_at
    assert [(check.check_name, check.status, check.summary) for check in checks] == [
        (
            PUBLISH_READINESS_CHECK_NAME,
            ServiceHealthStatus.FAIL,
            "enabled endpoint 'translate' must define upstream before publish",
        ),
    ]


async def test_publish_service_adds_pass_check_beside_earlier_failed_attempt(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
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
    await create_trusted_provider_records(db_session_factory, account_id=provider_account_id)

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

    checks = await _health_checks(db_session_factory, service_id=service_id)
    assert await _lifecycle(db_session_factory, service_id) is ServiceLifecycle.ACTIVE
    assert [(check.check_name, check.status) for check in checks] == [
        (DOMAIN_CONTROL_CHECK_NAME, ServiceHealthStatus.PASS),
        (PUBLISH_READINESS_CHECK_NAME, ServiceHealthStatus.PASS),
        (PUBLISH_READINESS_CHECK_NAME, ServiceHealthStatus.FAIL),
    ]


async def test_publish_without_a_signing_secret_records_a_failed_readiness_check(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="unsigned-service",
        trusted=False,
    )

    with pytest.raises(
        InvalidInputError,
        match="provider must create a signing secret before publish",
    ):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    checks = await _health_checks(db_session_factory, service_id=service_id)
    assert await _lifecycle(db_session_factory, service_id) is ServiceLifecycle.DRAFT
    assert [(check.check_name, check.status, check.summary) for check in checks] == [
        (
            PUBLISH_READINESS_CHECK_NAME,
            ServiceHealthStatus.FAIL,
            "provider must create a signing secret before publish",
        ),
    ]


async def test_publish_without_a_domain_token_records_a_failed_domain_control_check(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="unproven-service",
        trusted=False,
    )
    await create_signing_secret_record(db_session_factory, account_id=provider_account_id)

    with pytest.raises(InvalidInputError, match="has no domain verification token"):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    checks = await _health_checks(db_session_factory, service_id=service_id)
    assert await _lifecycle(db_session_factory, service_id) is ServiceLifecycle.DRAFT
    assert [(check.check_name, check.status) for check in checks] == [
        (DOMAIN_CONTROL_CHECK_NAME, ServiceHealthStatus.FAIL),
    ]


@pytest.mark.parametrize(
    ("addresses", "txt_values", "proof"),
    [
        ([TEST_UPSTREAM_ADDRESS], [], "record_missing"),
        ([TEST_UPSTREAM_ADDRESS], [record_value("another-accounts-token")], "record_missing"),
        ([TEST_UPSTREAM_ADDRESS, "10.0.0.1"], None, "not_public"),
    ],
    ids=["record_removed", "another_accounts_record", "rebound_to_a_private_address"],
)
async def test_publish_rechecks_the_host_live_and_stays_draft_when_it_fails(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
    addresses: list[str],
    txt_values: list[str] | None,
    proof: str,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="rechecked-service",
    )
    # The upstream was accepted at registration; its DNS changed before publishing.
    dns_resolver.addresses[TEST_UPSTREAM_HOST] = addresses
    if txt_values is not None:
        dns_resolver.txt_records[TEST_DOMAIN_RECORD_NAME] = txt_values

    with pytest.raises(
        InvalidInputError,
        match=f"failed the domain-control check: {TEST_UPSTREAM_HOST} \\({proof}\\)",
    ):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    checks = await _health_checks(db_session_factory, service_id=service_id)
    assert await _lifecycle(db_session_factory, service_id) is ServiceLifecycle.DRAFT
    assert [(check.check_name, check.status, check.details) for check in checks] == [
        (
            DOMAIN_CONTROL_CHECK_NAME,
            ServiceHealthStatus.FAIL,
            {"hosts": {TEST_UPSTREAM_HOST: proof}},
        ),
    ]


async def test_publish_proves_the_hosts_of_disabled_endpoints_too(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="partly-disabled-service",
    )
    # A disabled endpoint can be enabled after publishing, so its host is proven now.
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

    with pytest.raises(InvalidInputError, match=f"{OTHER_HOST} \\(record_missing\\)"):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    checks = await _health_checks(db_session_factory, service_id=service_id)
    assert checks[0].details == {
        "hosts": {OTHER_HOST: "record_missing", TEST_UPSTREAM_HOST: "verified"},
    }


async def test_publish_holds_no_transaction_open_during_the_domain_check(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="short-transaction-service",
    )

    async with db_session_factory() as session:
        watching = TransactionWatchingResolver(dns_resolver, session)
        await publishing.publish_service(
            session=session,
            resolver=watching,
            account_id=provider_account_id,
            service_id=service_id,
        )

    # The host's addresses, then its TXT record.
    assert watching.in_transaction_during_lookups == [False, False]
    assert await _lifecycle(db_session_factory, service_id) is ServiceLifecycle.ACTIVE


async def _repoint_the_enabled_upstream(
    session: AsyncSession,
    *,
    enabled_endpoint_id: int,
    disabled_endpoint_id: int,
) -> None:
    upstream = await session.get(ProviderUpstream, enabled_endpoint_id)
    assert upstream is not None
    upstream.base_url = f"https://{OTHER_HOST}/"


async def _add_an_upstream_on_the_disabled_endpoint(
    session: AsyncSession,
    *,
    enabled_endpoint_id: int,
    disabled_endpoint_id: int,
) -> None:
    session.add(
        ProviderUpstream(
            endpoint_id=disabled_endpoint_id,
            base_url=f"https://{OTHER_HOST}/",
            path="/invoke",
            http_method="POST",
        ),
    )


async def _remove_the_disabled_endpoints_upstream(
    session: AsyncSession,
    *,
    enabled_endpoint_id: int,
    disabled_endpoint_id: int,
) -> None:
    upstream = await session.get(ProviderUpstream, disabled_endpoint_id)
    assert upstream is not None
    await session.delete(upstream)


type _UpstreamChange = Callable[..., Awaitable[None]]


@pytest.mark.parametrize(
    ("change", "disabled_endpoint_has_upstream"),
    [
        pytest.param(_repoint_the_enabled_upstream, False, id="repoint"),
        pytest.param(_add_an_upstream_on_the_disabled_endpoint, False, id="add"),
        pytest.param(_remove_the_disabled_endpoints_upstream, True, id="remove"),
    ],
)
async def test_publish_rejects_upstreams_changed_during_the_domain_check(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
    change: _UpstreamChange,
    disabled_endpoint_has_upstream: bool,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="moving-service",
    )
    async with db_session_factory() as session:
        enabled_endpoint_id = await session.scalar(
            select(ServiceEndpoint.id).where(ServiceEndpoint.service_id == service_id),
        )
    assert enabled_endpoint_id is not None
    disabled_endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="later",
        is_enabled=False,
    )
    if disabled_endpoint_has_upstream:
        await create_upstream_record(
            db_session_factory,
            endpoint_id=disabled_endpoint_id,
            base_url=f"https://{OTHER_HOST}/",
        )
    dns_resolver.addresses[OTHER_HOST] = [TEST_UPSTREAM_ADDRESS]

    class _MovingResolver(FakeResolver):
        changed = False

        async def resolve_txt(self, name: str) -> list[str]:
            # The provider changes the upstreams once while the check runs.
            if not self.changed:
                self.changed = True
                async with db_session_factory.begin() as other_session:
                    await change(
                        other_session,
                        enabled_endpoint_id=enabled_endpoint_id,
                        disabled_endpoint_id=disabled_endpoint_id,
                    )
            return await super().resolve_txt(name)

    moving = _MovingResolver(dns_resolver.addresses, txt_records=dns_resolver.txt_records)

    with pytest.raises(ConflictError, match="upstreams changed while publishing; publish again"):
        await _publish(
            db_session_factory,
            resolver=moving,
            account_id=provider_account_id,
            service_id=service_id,
        )

    assert await _lifecycle(db_session_factory, service_id) is ServiceLifecycle.DRAFT
    assert await _health_checks(db_session_factory, service_id=service_id) == []


async def test_publish_rejects_an_upstream_with_no_host_before_any_lookup(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    provider_account_id = await create_provider_account_record(db_session_factory)
    service_id = await _seed_publishable_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="hostless-service",
        with_upstream=False,
    )
    async with db_session_factory() as session:
        endpoint_id = await session.scalar(
            select(ServiceEndpoint.id).where(ServiceEndpoint.service_id == service_id),
        )
    assert endpoint_id is not None
    # Stored outside the API, which only ever stores a validated https URL with a host.
    await create_upstream_record(
        db_session_factory, endpoint_id=endpoint_id, base_url="https:///v1"
    )

    with pytest.raises(
        InvalidInputError,
        match="endpoint 'translate' has an upstream with no host; set its upstream again",
    ):
        await _publish(
            db_session_factory,
            resolver=dns_resolver,
            account_id=provider_account_id,
            service_id=service_id,
        )

    assert dns_resolver.lookups == []
    assert await _lifecycle(db_session_factory, service_id) is ServiceLifecycle.DRAFT
