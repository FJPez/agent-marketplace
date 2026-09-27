from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from sqlalchemy import select
from tests.fixtures.settings import TEST_PRICE_TERMS
from tests.helpers.auth import create_account
from tests.helpers.dns import TEST_DOMAIN_TOKEN, TEST_UPSTREAM_BASE_URL

from app.core.config import get_settings
from app.core.enums import (
    AccessMode,
    ServiceHealthStatus,
    ServiceLifecycle,
)
from app.db.models import (
    ListingPrice,
    ModerationAction,
    ProviderDomainToken,
    ProviderUpstream,
    Service,
    ServiceEndpoint,
    ServiceHealthCheck,
    ServiceRevision,
    ServiceTag,
)
from app.services import provider_signing_secrets
from app.services.service_health import PUBLISH_READINESS_CHECK_NAME

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

type JsonObject = dict[str, object]


class ProviderAccountFactory(Protocol):
    def __call__(
        self,
        *,
        display_name: str = ...,
        wallet_address: str | None = ...,
        account_type: str = ...,
        is_admin: bool = ...,
    ) -> Awaitable[int]: ...


class ConsumerAccountFactory(Protocol):
    def __call__(
        self,
        *,
        display_name: str = ...,
        wallet_address: str | None = ...,
        account_type: str = ...,
        is_admin: bool = ...,
    ) -> Awaitable[int]: ...


class AdminAccountFactory(Protocol):
    def __call__(
        self,
        *,
        display_name: str = ...,
        wallet_address: str | None = ...,
        account_type: str = ...,
    ) -> Awaitable[int]: ...


class ServiceFactory(Protocol):
    def __call__(
        self,
        *,
        provider_account_id: int,
        slug: str = ...,
        name: str | None = ...,
        summary: str | None = ...,
        description: str | None | object = ...,
        lifecycle: ServiceLifecycle = ...,
        with_revision: bool = ...,
        revision_number: int = ...,
        change_token: str = ...,
        snapshot: JsonObject | None = ...,
        tags: list[str] | None = ...,
    ) -> Awaitable[int]: ...


class RevisionFactory(Protocol):
    def __call__(
        self,
        *,
        service_id: int,
        revision_number: int = ...,
        change_token: str = ...,
        snapshot: JsonObject | None = ...,
        set_current: bool = ...,
    ) -> Awaitable[int]: ...


class EndpointFactory(Protocol):
    def __call__(
        self,
        *,
        service_id: int,
        key: str = ...,
        name: str | None = ...,
        summary: str | None | object = ...,
        description: str | None | object = ...,
        access_mode: AccessMode = ...,
        request_schema: JsonObject | None = ...,
        response_schema: JsonObject | None = ...,
        timeout_seconds: int = ...,
        is_enabled: bool = ...,
    ) -> Awaitable[int]: ...


class UpstreamFactory(Protocol):
    def __call__(
        self,
        *,
        endpoint_id: int,
        base_url: str = ...,
        path: str = ...,
        http_method: str = ...,
    ) -> Awaitable[int]: ...


class ModerationActionFactory(Protocol):
    def __call__(
        self,
        *,
        service_id: int,
        action: str,
        actor_account_id: int | None = ...,
        reason: str = ...,
    ) -> Awaitable[int]: ...


_UNSET = object()


async def create_provider_account_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    display_name: str = "Provider",
    wallet_address: str | None = None,
    account_type: str = "human",
    is_admin: bool = False,
) -> int:
    return await create_account(
        db_session_factory,
        wallet_address=wallet_address,
        display_name=display_name,
        account_type=account_type,
        is_admin=is_admin,
    )


async def create_consumer_account_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    display_name: str = "Consumer",
    wallet_address: str | None = None,
    account_type: str = "human",
    is_admin: bool = False,
) -> int:
    return await create_account(
        db_session_factory,
        wallet_address=wallet_address,
        display_name=display_name,
        account_type=account_type,
        is_admin=is_admin,
    )


async def create_admin_account_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    display_name: str = "Admin",
    wallet_address: str | None = None,
    account_type: str = "human",
) -> int:
    return await create_account(
        db_session_factory,
        wallet_address=wallet_address,
        display_name=display_name,
        account_type=account_type,
        is_admin=True,
    )


async def create_service_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str = "service",
    name: str | None = None,
    summary: str | None = None,
    description: str | None | object = _UNSET,
    lifecycle: ServiceLifecycle = ServiceLifecycle.ACTIVE,
    with_revision: bool = False,
    revision_number: int = 1,
    change_token: str = "c" * 64,
    snapshot: dict[str, object] | None = None,
    tags: list[str] | None = None,
) -> int:
    resolved_description = f"{slug} description" if description is _UNSET else description
    async with db_session_factory.begin() as session:
        service = Service(
            provider_account_id=provider_account_id,
            slug=slug,
            name=name or f"{slug} name",
            summary=summary or f"{slug} summary",
            description=resolved_description,
            lifecycle=lifecycle,
        )
        session.add(service)
        await session.flush()

        if tags:
            session.add_all(
                [ServiceTag(service_id=service.id, tag=tag) for tag in tags],
            )

        if with_revision:
            revision = ServiceRevision(
                service_id=service.id,
                revision_number=revision_number,
                change_token=change_token,
                snapshot=snapshot or {"slug": slug},
            )
            session.add(revision)
            await session.flush()
            service.current_revision_id = revision.id
            service.current_change_token = revision.change_token

        return service.id


async def create_revision_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    revision_number: int = 1,
    change_token: str = "c" * 64,
    snapshot: dict[str, object] | None = None,
    set_current: bool = True,
) -> int:
    async with db_session_factory.begin() as session:
        service = await session.get(Service, service_id)
        assert service is not None
        revision = ServiceRevision(
            service_id=service_id,
            revision_number=revision_number,
            change_token=change_token,
            snapshot=snapshot or {"slug": service.slug},
        )
        session.add(revision)
        await session.flush()
        if set_current:
            service.current_revision_id = revision.id
            service.current_change_token = revision.change_token
        return revision.id


async def create_endpoint_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    key: str = "translate",
    name: str | None = None,
    summary: str | None | object = _UNSET,
    description: str | None | object = _UNSET,
    access_mode: AccessMode = AccessMode.FREE,
    request_schema: dict[str, object] | None = None,
    response_schema: dict[str, object] | None = None,
    timeout_seconds: int = 30,
    is_enabled: bool = True,
) -> int:
    resolved_summary = f"{key} summary" if summary is _UNSET else summary
    resolved_description = f"{key} description" if description is _UNSET else description
    async with db_session_factory.begin() as session:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key=key,
            name=name or key.title(),
            summary=resolved_summary,
            description=resolved_description,
            access_mode=access_mode,
            request_schema=request_schema or {"type": "object"},
            response_schema=response_schema or {"type": "object"},
            timeout_seconds=timeout_seconds,
            is_enabled=is_enabled,
        )
        session.add(endpoint)
        await session.flush()
        return endpoint.id


async def create_listing_price_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    amount: int = 250_000,
    version: int = 1,
) -> int:
    """Store a price version with the default test payment terms and make it current."""
    async with db_session_factory.begin() as session:
        price = ListingPrice(
            endpoint_id=endpoint_id,
            version=version,
            amount=amount,
            **TEST_PRICE_TERMS,
        )
        session.add(price)
        # The get autoflushes the pending version, which assigns its id.
        endpoint = await session.get(ServiceEndpoint, endpoint_id)
        assert endpoint is not None
        endpoint.current_price_id = price.id
        return price.id


async def read_price_versions(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
) -> tuple[int | None, list[tuple[int, int]]]:
    """Return the current version (None when unpriced) and every stored (version, amount)."""
    async with db_session_factory() as session:
        current_version = await session.scalar(
            select(ListingPrice.version)
            .join(ServiceEndpoint, ServiceEndpoint.current_price_id == ListingPrice.id)
            .where(ServiceEndpoint.id == endpoint_id),
        )
        rows = await session.execute(
            select(ListingPrice.version, ListingPrice.amount)
            .where(ListingPrice.endpoint_id == endpoint_id)
            .order_by(ListingPrice.version),
        )
        return current_version, [(row.version, row.amount) for row in rows]


async def create_upstream_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    base_url: str = TEST_UPSTREAM_BASE_URL,
    path: str = "/invoke",
    http_method: str = "POST",
) -> int:
    async with db_session_factory.begin() as session:
        upstream = ProviderUpstream(
            endpoint_id=endpoint_id,
            base_url=base_url,
            path=path,
            http_method=http_method,
        )
        session.add(upstream)
        await session.flush()
        return endpoint_id


async def create_signing_secret_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
) -> None:
    """Issue the account's signing secret, as a provider does before publishing."""
    async with db_session_factory() as session:
        await provider_signing_secrets.create_signing_secret(
            session=session,
            settings=get_settings(),
            account_id=account_id,
        )


async def create_trusted_provider_records(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
) -> None:
    """Give the account what publishing asks of a provider.

    A signing secret, and the test domain token, whose TXT record the `dns_resolver`
    fixture serves for the test upstream host.
    """
    await create_signing_secret_record(db_session_factory, account_id=account_id)
    async with db_session_factory.begin() as session:
        session.add(ProviderDomainToken(account_id=account_id, token=TEST_DOMAIN_TOKEN))


async def create_moderation_action_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    action: str,
    actor_account_id: int | None = None,
    reason: str = "policy",
) -> int:
    async with db_session_factory.begin() as session:
        moderation_action = ModerationAction(
            service_id=service_id,
            actor_account_id=actor_account_id,
            action=action,
            reason=reason,
        )
        session.add(moderation_action)
        await session.flush()
        return moderation_action.id


async def create_health_check_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
    status: ServiceHealthStatus,
    check_name: str = PUBLISH_READINESS_CHECK_NAME,
    summary: str = "unhealthy",
    details: JsonObject | None = None,
) -> int:
    async with db_session_factory.begin() as session:
        health_check = ServiceHealthCheck(
            service_id=service_id,
            check_name=check_name,
            status=status,
            summary=summary,
            details=details or {"source": "test"},
        )
        session.add(health_check)
        await session.flush()
        return health_check.id


@pytest.fixture
def provider_account_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> ProviderAccountFactory:
    async def create_provider_account(
        *,
        display_name: str = "Provider",
        wallet_address: str | None = None,
        account_type: str = "human",
        is_admin: bool = False,
    ) -> int:
        return await create_provider_account_record(
            db_session_factory,
            wallet_address=wallet_address,
            display_name=display_name,
            account_type=account_type,
            is_admin=is_admin,
        )

    return create_provider_account


@pytest.fixture
def consumer_account_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> ConsumerAccountFactory:
    async def create_consumer_account(
        *,
        display_name: str = "Consumer",
        wallet_address: str | None = None,
        account_type: str = "human",
        is_admin: bool = False,
    ) -> int:
        return await create_consumer_account_record(
            db_session_factory,
            wallet_address=wallet_address,
            display_name=display_name,
            account_type=account_type,
            is_admin=is_admin,
        )

    return create_consumer_account


@pytest.fixture
def admin_account_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AdminAccountFactory:
    async def create_admin_account(
        *,
        display_name: str = "Admin",
        wallet_address: str | None = None,
        account_type: str = "human",
    ) -> int:
        return await create_admin_account_record(
            db_session_factory,
            wallet_address=wallet_address,
            display_name=display_name,
            account_type=account_type,
        )

    return create_admin_account


@pytest.fixture
def service_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> ServiceFactory:
    async def create_service(
        *,
        provider_account_id: int,
        slug: str = "service",
        name: str | None = None,
        summary: str | None = None,
        description: str | None | object = _UNSET,
        lifecycle: ServiceLifecycle = ServiceLifecycle.ACTIVE,
        with_revision: bool = False,
        revision_number: int = 1,
        change_token: str = "c" * 64,
        snapshot: JsonObject | None = None,
        tags: list[str] | None = None,
    ) -> int:
        return await create_service_record(
            db_session_factory,
            provider_account_id=provider_account_id,
            slug=slug,
            name=name,
            summary=summary,
            description=description,
            lifecycle=lifecycle,
            with_revision=with_revision,
            revision_number=revision_number,
            change_token=change_token,
            snapshot=snapshot,
            tags=tags,
        )

    return create_service


@pytest.fixture
def revision_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> RevisionFactory:
    async def create_revision(
        *,
        service_id: int,
        revision_number: int = 1,
        change_token: str = "c" * 64,
        snapshot: JsonObject | None = None,
        set_current: bool = True,
    ) -> int:
        return await create_revision_record(
            db_session_factory,
            service_id=service_id,
            revision_number=revision_number,
            change_token=change_token,
            snapshot=snapshot,
            set_current=set_current,
        )

    return create_revision


@pytest.fixture
def endpoint_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> EndpointFactory:
    async def create_endpoint(
        *,
        service_id: int,
        key: str = "translate",
        name: str | None = None,
        summary: str | None | object = _UNSET,
        description: str | None | object = _UNSET,
        access_mode: AccessMode = AccessMode.FREE,
        request_schema: JsonObject | None = None,
        response_schema: JsonObject | None = None,
        timeout_seconds: int = 30,
        is_enabled: bool = True,
    ) -> int:
        return await create_endpoint_record(
            db_session_factory,
            service_id=service_id,
            key=key,
            name=name,
            summary=summary,
            description=description,
            access_mode=access_mode,
            request_schema=request_schema,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds,
            is_enabled=is_enabled,
        )

    return create_endpoint


@pytest.fixture
def upstream_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> UpstreamFactory:
    async def create_upstream(
        *,
        endpoint_id: int,
        base_url: str = TEST_UPSTREAM_BASE_URL,
        path: str = "/invoke",
        http_method: str = "POST",
    ) -> int:
        return await create_upstream_record(
            db_session_factory,
            endpoint_id=endpoint_id,
            base_url=base_url,
            path=path,
            http_method=http_method,
        )

    return create_upstream


@pytest.fixture
def moderation_action_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> ModerationActionFactory:
    async def create_moderation_action(
        *,
        service_id: int,
        action: str,
        actor_account_id: int | None = None,
        reason: str = "policy",
    ) -> int:
        return await create_moderation_action_record(
            db_session_factory,
            service_id=service_id,
            action=action,
            actor_account_id=actor_account_id,
            reason=reason,
        )

    return create_moderation_action
