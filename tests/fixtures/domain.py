from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from tests.helpers.auth import create_account

from app.core.enums import PricingModelType, ServiceHealthStatus, ServiceLifecycle
from app.core.request_hash import hash_request_body
from app.db.models import (
    ModerationAction,
    PricingModel,
    ProviderUpstream,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceHealthCheck,
    ServiceRevision,
    ServiceTag,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.enums import AccessMode

_UNSET = object()
_DEFAULT_UPSTREAM_CONFIG = {
    "auth": {
        "type": "hmac_sha256",
        "key_id": "gateway-key",
        "secret": "super-secret",
    },
}


async def _ensure_service_revision(
    session: AsyncSession,
    *,
    service: Service,
) -> ServiceRevision:
    if service.current_revision_id is not None and service.current_change_token is not None:
        revision = await session.get(ServiceRevision, service.current_revision_id)
        if revision is not None:
            return revision

    revision = ServiceRevision(
        service_id=service.id,
        revision_number=1,
        change_token="c" * 64,
        snapshot={"slug": service.slug},
    )
    session.add(revision)
    await session.flush()
    service.current_revision_id = revision.id
    service.current_change_token = revision.change_token
    return revision


@pytest.fixture
def provider_account_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_provider_account(
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

    return create_provider_account


@pytest.fixture
def consumer_account_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_consumer_account(
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

    return create_consumer_account


@pytest.fixture
def admin_account_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_admin_account(
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

    return create_admin_account


@pytest.fixture
def service_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
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

    return create_service


@pytest.fixture
def revision_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_revision(
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

    return create_revision


@pytest.fixture
def endpoint_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_endpoint(
        *,
        service_id: int,
        key: str = "translate",
        name: str | None = None,
        summary: str | None | object = _UNSET,
        description: str | None | object = _UNSET,
        access_mode: AccessMode,
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

    return create_endpoint


@pytest.fixture
def pricing_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_pricing(
        *,
        endpoint_id: int,
        pricing_type: PricingModelType = PricingModelType.FIXED_PER_CALL,
        amount_minor: int | None = 500,
        currency: str | None = "USD",
    ) -> int:
        async with db_session_factory.begin() as session:
            pricing = PricingModel(
                endpoint_id=endpoint_id,
                pricing_type=pricing_type,
                amount_minor=amount_minor,
                currency=currency,
            )
            session.add(pricing)
            await session.flush()
            return pricing.id

    return create_pricing


@pytest.fixture
def upstream_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_upstream(
        *,
        endpoint_id: int,
        base_url: str = "https://provider.internal",
        path: str = "/invoke",
        http_method: str = "POST",
        config: dict[str, object] | None = None,
    ) -> int:
        async with db_session_factory.begin() as session:
            upstream = ProviderUpstream(
                endpoint_id=endpoint_id,
                base_url=base_url,
                path=path,
                http_method=http_method,
                config=config or dict(_DEFAULT_UPSTREAM_CONFIG),
            )
            session.add(upstream)
            await session.flush()
            return upstream.id

    return create_upstream


@pytest.fixture
def quote_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_quote(
        *,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str = "translate",
        payload: dict[str, object] | None = None,
        request_hash: str | None = None,
        pricing_type: PricingModelType | None = None,
        amount_minor: int | None | object = _UNSET,
        currency: str | None | object = _UNSET,
        service_revision_id: int | None = None,
        service_change_token: str | None = None,
        expires_at: datetime | None = None,
    ) -> int:
        request_payload = payload or {"text": "hello"}
        async with db_session_factory.begin() as session:
            service = await session.get(Service, service_id)
            endpoint = await session.get(ServiceEndpoint, endpoint_id)
            assert service is not None
            assert endpoint is not None

            revision_id = service_revision_id
            change_token = service_change_token
            if revision_id is None or change_token is None:
                revision = await _ensure_service_revision(session, service=service)
                revision_id = revision.id
                change_token = revision.change_token

            pricing = await session.scalar(
                select(PricingModel).where(PricingModel.endpoint_id == endpoint_id),
            )
            resolved_pricing_type = pricing_type or (
                pricing.pricing_type if pricing is not None else PricingModelType.FREE
            )
            resolved_amount_minor = (
                pricing.amount_minor
                if amount_minor is _UNSET and pricing is not None
                else (None if amount_minor is _UNSET else amount_minor)
            )
            resolved_currency = (
                pricing.currency
                if currency is _UNSET and pricing is not None
                else (None if currency is _UNSET else currency)
            )

            quote = Quote(
                service_id=service_id,
                endpoint_id=endpoint_id,
                endpoint_key=endpoint_key,
                request_hash=request_hash or hash_request_body(request_payload),
                pricing_type=resolved_pricing_type,
                amount_minor=resolved_amount_minor,
                currency=resolved_currency,
                service_revision_id=revision_id,
                service_change_token=change_token,
                expires_at=expires_at or (datetime.now(UTC) + timedelta(minutes=5)),
            )
            session.add(quote)
            await session.flush()
            return quote.id

    return create_quote


@pytest.fixture
def moderation_action_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_moderation_action(
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

    return create_moderation_action


@pytest.fixture
def health_check_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Callable[..., object]:
    async def create_health_check(
        *,
        service_id: int,
        status: ServiceHealthStatus,
        check_name: str = "publish-readiness",
        summary: str = "unhealthy",
        details: dict[str, object] | None = None,
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

    return create_health_check
