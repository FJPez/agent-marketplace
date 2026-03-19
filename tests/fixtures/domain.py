from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import pytest
from sqlalchemy import select
from tests.helpers.auth import create_account

from app.core.enums import (
    AccessMode,
    InvocationFailureReason,
    InvocationStatus,
    LedgerEntryType,
    PaymentAttemptStatus,
    PayoutFailureCode,
    PayoutStatus,
    PricingModelType,
    ServiceHealthStatus,
    ServiceLifecycle,
)
from app.core.request_hash import hash_request_body
from app.db.models import (
    Invocation,
    LedgerEntry,
    ModerationAction,
    PaymentAttempt,
    Payout,
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


class PricingFactory(Protocol):
    def __call__(
        self,
        *,
        endpoint_id: int,
        pricing_type: PricingModelType = ...,
        amount_minor: int | None = ...,
        currency: str | None = ...,
    ) -> Awaitable[int]: ...


class UpstreamFactory(Protocol):
    def __call__(
        self,
        *,
        endpoint_id: int,
        base_url: str = ...,
        path: str = ...,
        http_method: str = ...,
        config: JsonObject | None = ...,
    ) -> Awaitable[int]: ...


class QuoteFactory(Protocol):
    def __call__(
        self,
        *,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str = ...,
        payload: JsonObject | None = ...,
        request_hash: str | None = ...,
        pricing_type: PricingModelType | None = ...,
        amount_minor: int | None | object = ...,
        currency: str | None | object = ...,
        service_revision_id: int | None = ...,
        service_change_token: str | None = ...,
        expires_at: datetime | None = ...,
    ) -> Awaitable[int]: ...


class InvocationFactory(Protocol):
    def __call__(
        self,
        *,
        consumer_account_id: int,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str = ...,
        access_mode: AccessMode = ...,
        quote_id: int | None = ...,
        payload: JsonObject | None = ...,
        request_hash: str | None = ...,
        idempotency_key: str = ...,
        status: InvocationStatus = ...,
        response_payload: JsonObject | None = ...,
        upstream_status_code: int | None = ...,
        error_message: str | None = ...,
        failure_reason: InvocationFailureReason | None = ...,
    ) -> Awaitable[int]: ...


class PaymentAttemptFactory(Protocol):
    def __call__(
        self,
        *,
        consumer_account_id: int,
        quote_id: int,
        invocation_id: int | None = ...,
        idempotency_key: str = ...,
        payment_identifier: str | None = ...,
        status: PaymentAttemptStatus = ...,
        payment_requirement: JsonObject | None = ...,
        payment_payload: JsonObject | None = ...,
        verify_outcome: JsonObject | None = ...,
        settle_outcome: JsonObject | None = ...,
        facilitator_reference: str | None = ...,
    ) -> Awaitable[int]: ...


class LedgerEntryFactory(Protocol):
    def __call__(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        entry_type: LedgerEntryType = ...,
        amount_minor: int = ...,
        currency: str = ...,
    ) -> Awaitable[int]: ...


class PayoutFactory(Protocol):
    def __call__(
        self,
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        destination_wallet: str | None = ...,
        amount_minor: int = ...,
        currency: str = ...,
        network: str = ...,
        status: PayoutStatus = ...,
        transfer_reference: str | None = ...,
        request_idempotency_key: str | None = ...,
        failure_code: PayoutFailureCode | None = ...,
        error_message: str | None = ...,
        attempt_count: int = ...,
        prepared_raw_transaction: str | None = ...,
        chain_nonce: int | None = ...,
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


class HealthCheckFactory(Protocol):
    def __call__(
        self,
        *,
        service_id: int,
        status: ServiceHealthStatus,
        check_name: str = ...,
        summary: str = ...,
        details: JsonObject | None = ...,
    ) -> Awaitable[int]: ...


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


async def create_pricing_record(
    db_session_factory: async_sessionmaker[AsyncSession],
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
        return endpoint_id


async def create_upstream_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    endpoint_id: int,
    base_url: str = "http://127.0.0.1:9000",
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
        return endpoint_id


async def create_quote_record(
    db_session_factory: async_sessionmaker[AsyncSession],
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


async def create_invocation_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    consumer_account_id: int,
    service_id: int,
    endpoint_id: int,
    endpoint_key: str = "translate",
    access_mode: AccessMode = AccessMode.FREE,
    quote_id: int | None = None,
    payload: JsonObject | None = None,
    request_hash: str | None = None,
    idempotency_key: str = "invoke-key",
    status: InvocationStatus = InvocationStatus.SUCCEEDED,
    response_payload: JsonObject | None = None,
    upstream_status_code: int | None = 200,
    error_message: str | None = None,
    failure_reason: InvocationFailureReason | None = None,
) -> int:
    request_payload = payload or {"text": "hello"}
    async with db_session_factory.begin() as session:
        invocation = Invocation(
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key=endpoint_key,
            access_mode=access_mode,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash
            or hash_request_body(
                {
                    "service_id": service_id,
                    "endpoint_key": endpoint_key,
                    "payload": request_payload,
                    "quote_id": quote_id,
                }
            ),
            status=status,
            response_payload=response_payload,
            upstream_status_code=upstream_status_code,
            error_message=error_message,
            failure_reason=failure_reason,
        )
        session.add(invocation)
        await session.flush()
        return invocation.id


async def create_payment_attempt_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    consumer_account_id: int,
    quote_id: int,
    invocation_id: int | None = None,
    idempotency_key: str = "invoke-key",
    payment_identifier: str | None = "payment-1",
    status: PaymentAttemptStatus = PaymentAttemptStatus.CHALLENGED,
    payment_requirement: JsonObject | None = None,
    payment_payload: JsonObject | None = None,
    verify_outcome: JsonObject | None = None,
    settle_outcome: JsonObject | None = None,
    facilitator_reference: str | None = None,
) -> int:
    async with db_session_factory.begin() as session:
        attempt = PaymentAttempt(
            consumer_account_id=consumer_account_id,
            quote_id=quote_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            payment_identifier=payment_identifier,
            status=status,
            payment_requirement=payment_requirement or {"amount_minor": 500},
            payment_payload=payment_payload or {"payment_identifier": payment_identifier},
            verify_outcome=verify_outcome,
            settle_outcome=settle_outcome,
            facilitator_reference=facilitator_reference,
        )
        session.add(attempt)
        await session.flush()
        return attempt.id


async def create_ledger_entry_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    service_id: int,
    invocation_id: int,
    payment_attempt_id: int,
    entry_type: LedgerEntryType = LedgerEntryType.CHARGE,
    amount_minor: int = 500,
    currency: str = "USD",
) -> int:
    async with db_session_factory.begin() as session:
        entry = LedgerEntry(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            entry_type=entry_type,
            amount_minor=amount_minor,
            currency=currency,
        )
        session.add(entry)
        await session.flush()
        return entry.id


async def create_payout_record(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    service_id: int,
    invocation_id: int,
    payment_attempt_id: int,
    destination_wallet: str | None = None,
    amount_minor: int = 450,
    currency: str = "USDC",
    network: str = "base-sepolia",
    status: PayoutStatus = PayoutStatus.READY,
    transfer_reference: str | None = None,
    request_idempotency_key: str | None = None,
    failure_code: PayoutFailureCode | None = None,
    error_message: str | None = None,
    attempt_count: int = 0,
    prepared_raw_transaction: str | None = None,
    chain_nonce: int | None = None,
) -> int:
    async with db_session_factory.begin() as session:
        payout = Payout(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet=destination_wallet,
            amount_minor=amount_minor,
            currency=currency,
            network=network,
            status=status,
            transfer_reference=transfer_reference,
            request_idempotency_key=request_idempotency_key,
            failure_code=failure_code,
            error_message=error_message,
            attempt_count=attempt_count,
            prepared_raw_transaction=prepared_raw_transaction,
            chain_nonce=chain_nonce,
        )
        session.add(payout)
        await session.flush()
        return payout.id


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
def pricing_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> PricingFactory:
    async def create_pricing(
        *,
        endpoint_id: int,
        pricing_type: PricingModelType = PricingModelType.FIXED_PER_CALL,
        amount_minor: int | None = 500,
        currency: str | None = "USD",
    ) -> int:
        return await create_pricing_record(
            db_session_factory,
            endpoint_id=endpoint_id,
            pricing_type=pricing_type,
            amount_minor=amount_minor,
            currency=currency,
        )

    return create_pricing


@pytest.fixture
def upstream_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> UpstreamFactory:
    async def create_upstream(
        *,
        endpoint_id: int,
        base_url: str = "http://127.0.0.1:9000",
        path: str = "/invoke",
        http_method: str = "POST",
        config: JsonObject | None = None,
    ) -> int:
        return await create_upstream_record(
            db_session_factory,
            endpoint_id=endpoint_id,
            base_url=base_url,
            path=path,
            http_method=http_method,
            config=config,
        )

    return create_upstream


@pytest.fixture
def quote_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> QuoteFactory:
    async def create_quote(
        *,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str = "translate",
        payload: JsonObject | None = None,
        request_hash: str | None = None,
        pricing_type: PricingModelType | None = None,
        amount_minor: int | None | object = _UNSET,
        currency: str | None | object = _UNSET,
        service_revision_id: int | None = None,
        service_change_token: str | None = None,
        expires_at: datetime | None = None,
    ) -> int:
        return await create_quote_record(
            db_session_factory,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key=endpoint_key,
            payload=payload,
            request_hash=request_hash,
            pricing_type=pricing_type,
            amount_minor=amount_minor,
            currency=currency,
            service_revision_id=service_revision_id,
            service_change_token=service_change_token,
            expires_at=expires_at,
        )

    return create_quote


@pytest.fixture
def invocation_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> InvocationFactory:
    async def create_invocation(
        *,
        consumer_account_id: int,
        service_id: int,
        endpoint_id: int,
        endpoint_key: str = "translate",
        access_mode: AccessMode = AccessMode.FREE,
        quote_id: int | None = None,
        payload: JsonObject | None = None,
        request_hash: str | None = None,
        idempotency_key: str = "invoke-key",
        status: InvocationStatus = InvocationStatus.SUCCEEDED,
        response_payload: JsonObject | None = None,
        upstream_status_code: int | None = 200,
        error_message: str | None = None,
        failure_reason: InvocationFailureReason | None = None,
    ) -> int:
        return await create_invocation_record(
            db_session_factory,
            consumer_account_id=consumer_account_id,
            service_id=service_id,
            endpoint_id=endpoint_id,
            endpoint_key=endpoint_key,
            access_mode=access_mode,
            quote_id=quote_id,
            payload=payload,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            status=status,
            response_payload=response_payload,
            upstream_status_code=upstream_status_code,
            error_message=error_message,
            failure_reason=failure_reason,
        )

    return create_invocation


@pytest.fixture
def payment_attempt_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> PaymentAttemptFactory:
    async def create_payment_attempt(
        *,
        consumer_account_id: int,
        quote_id: int,
        invocation_id: int | None = None,
        idempotency_key: str = "invoke-key",
        payment_identifier: str | None = "payment-1",
        status: PaymentAttemptStatus = PaymentAttemptStatus.CHALLENGED,
        payment_requirement: JsonObject | None = None,
        payment_payload: JsonObject | None = None,
        verify_outcome: JsonObject | None = None,
        settle_outcome: JsonObject | None = None,
        facilitator_reference: str | None = None,
    ) -> int:
        return await create_payment_attempt_record(
            db_session_factory,
            consumer_account_id=consumer_account_id,
            quote_id=quote_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            payment_identifier=payment_identifier,
            status=status,
            payment_requirement=payment_requirement,
            payment_payload=payment_payload,
            verify_outcome=verify_outcome,
            settle_outcome=settle_outcome,
            facilitator_reference=facilitator_reference,
        )

    return create_payment_attempt


@pytest.fixture
def ledger_entry_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> LedgerEntryFactory:
    async def create_ledger_entry(
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        entry_type: LedgerEntryType = LedgerEntryType.CHARGE,
        amount_minor: int = 500,
        currency: str = "USD",
    ) -> int:
        return await create_ledger_entry_record(
            db_session_factory,
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            entry_type=entry_type,
            amount_minor=amount_minor,
            currency=currency,
        )

    return create_ledger_entry


@pytest.fixture
def payout_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> PayoutFactory:
    async def create_payout(
        *,
        provider_account_id: int,
        service_id: int,
        invocation_id: int,
        payment_attempt_id: int,
        destination_wallet: str | None = None,
        amount_minor: int = 450,
        currency: str = "USDC",
        network: str = "base-sepolia",
        status: PayoutStatus = PayoutStatus.READY,
        transfer_reference: str | None = None,
        request_idempotency_key: str | None = None,
        failure_code: PayoutFailureCode | None = None,
        error_message: str | None = None,
        attempt_count: int = 0,
        prepared_raw_transaction: str | None = None,
        chain_nonce: int | None = None,
    ) -> int:
        return await create_payout_record(
            db_session_factory,
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet=destination_wallet,
            amount_minor=amount_minor,
            currency=currency,
            network=network,
            status=status,
            transfer_reference=transfer_reference,
            request_idempotency_key=request_idempotency_key,
            failure_code=failure_code,
            error_message=error_message,
            attempt_count=attempt_count,
            prepared_raw_transaction=prepared_raw_transaction,
            chain_nonce=chain_nonce,
        )

    return create_payout


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


@pytest.fixture
def health_check_factory(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> HealthCheckFactory:
    async def create_health_check(
        *,
        service_id: int,
        status: ServiceHealthStatus,
        check_name: str = "publish-readiness",
        summary: str = "unhealthy",
        details: JsonObject | None = None,
    ) -> int:
        return await create_health_check_record(
            db_session_factory,
            service_id=service_id,
            status=status,
            check_name=check_name,
            summary=summary,
            details=details,
        )

    return create_health_check
