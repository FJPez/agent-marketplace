from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.config import get_settings
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models import (
    Account,
    ConsumerProfile,
    PricingModel,
    ProviderProfile,
    ProviderUpstream,
    Service,
    ServiceEndpoint,
    ServiceRevision,
    ServiceTag,
)
from app.db.session import create_engine, create_session_factory

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEMO_PROVIDER_NAME = "Demo Provider"
DEMO_CONSUMER_NAME = "Demo Consumer"
DEMO_SERVICE_SLUG = "demo-agent-service"
DEMO_CHANGE_TOKEN = "d" * 64
FREE_ENDPOINT_KEY = "free-ping"
PAID_ENDPOINT_KEY = "paid-summary"


@dataclass(frozen=True, slots=True)
class SeedResult:
    provider_account_id: int
    consumer_account_id: int
    service_id: int
    free_endpoint_id: int
    paid_endpoint_id: int


async def _get_or_create_account(session: AsyncSession) -> Account:
    account = Account()
    session.add(account)
    await session.flush()
    return account


async def _get_or_create_provider(session: AsyncSession) -> ProviderProfile:
    provider = await session.scalar(
        select(ProviderProfile).where(ProviderProfile.display_name == DEMO_PROVIDER_NAME),
    )
    if provider is not None:
        return provider

    account = await _get_or_create_account(session)
    provider = ProviderProfile(
        account_id=account.id,
        display_name=DEMO_PROVIDER_NAME,
    )
    session.add(provider)
    await session.flush()
    return provider


async def _get_or_create_consumer(session: AsyncSession) -> ConsumerProfile:
    consumer = await session.scalar(
        select(ConsumerProfile).where(ConsumerProfile.display_name == DEMO_CONSUMER_NAME),
    )
    if consumer is not None:
        return consumer

    account = await _get_or_create_account(session)
    consumer = ConsumerProfile(
        account_id=account.id,
        display_name=DEMO_CONSUMER_NAME,
    )
    session.add(consumer)
    await session.flush()
    return consumer


async def _get_or_create_service(session: AsyncSession, *, provider_account_id: int) -> Service:
    service = await session.scalar(select(Service).where(Service.slug == DEMO_SERVICE_SLUG))
    if service is None:
        service = Service(
            provider_account_id=provider_account_id,
            slug=DEMO_SERVICE_SLUG,
            name="Demo Agent Service",
            summary="Free and paid demo endpoints for manual marketplace testing.",
            description=(
                "A seeded service for manual testing of discovery, quoting, free invoke, "
                "and paid invoke flows."
            ),
            lifecycle=ServiceLifecycle.ACTIVE,
        )
        session.add(service)
        await session.flush()

    service.provider_account_id = provider_account_id
    service.name = "Demo Agent Service"
    service.summary = "Free and paid demo endpoints for manual marketplace testing."
    service.description = (
        "A seeded service for manual testing of discovery, quoting, free invoke, and paid "
        "invoke flows."
    )
    service.lifecycle = ServiceLifecycle.ACTIVE
    await session.flush()
    return service


async def _replace_tags(session: AsyncSession, *, service_id: int) -> None:
    existing_tags = await session.scalars(
        select(ServiceTag).where(ServiceTag.service_id == service_id),
    )
    for tag in existing_tags:
        await session.delete(tag)

    session.add_all(
        [
            ServiceTag(service_id=service_id, tag="demo"),
            ServiceTag(service_id=service_id, tag="manual-testing"),
        ],
    )
    await session.flush()


async def _get_or_create_endpoint(
    session: AsyncSession,
    *,
    service_id: int,
    key: str,
    access_mode: AccessMode,
    name: str,
    summary: str,
    timeout_seconds: int,
) -> ServiceEndpoint:
    endpoint = await session.scalar(
        select(ServiceEndpoint).where(
            ServiceEndpoint.service_id == service_id,
            ServiceEndpoint.key == key,
        ),
    )
    if endpoint is None:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key=key,
            name=name,
            summary=summary,
            description=summary,
            access_mode=access_mode,
            request_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            response_schema={
                "type": "object",
                "properties": {
                    "result": {"type": "string"},
                },
                "required": ["result"],
                "additionalProperties": False,
            },
            timeout_seconds=timeout_seconds,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()

    endpoint.name = name
    endpoint.summary = summary
    endpoint.description = summary
    endpoint.access_mode = access_mode
    endpoint.timeout_seconds = timeout_seconds
    endpoint.is_enabled = True
    await session.flush()
    return endpoint


async def _upsert_upstream(
    session: AsyncSession,
    *,
    base_url: str,
    endpoint_id: int,
    path: str,
) -> None:
    upstream = await session.get(ProviderUpstream, endpoint_id)
    if upstream is None:
        upstream = ProviderUpstream(
            endpoint_id=endpoint_id,
            base_url=base_url,
            path=path,
            http_method="POST",
            config={},
        )
        session.add(upstream)

    upstream.base_url = base_url
    upstream.path = path
    upstream.http_method = "POST"
    upstream.config = {
        "auth": {
            "type": "hmac_sha256",
            "key_id": "demo-key",
            "secret": "demo-secret",
        },
    }
    await session.flush()


async def _upsert_pricing(
    session: AsyncSession,
    *,
    endpoint_id: int,
    pricing_type: PricingModelType,
    amount_minor: int | None,
    currency: str | None,
) -> None:
    pricing = await session.get(PricingModel, endpoint_id)
    if pricing is None:
        pricing = PricingModel(
            endpoint_id=endpoint_id,
            pricing_type=pricing_type,
            amount_minor=amount_minor,
            currency=currency,
        )
        session.add(pricing)
        await session.flush()
        return

    pricing.pricing_type = pricing_type
    pricing.amount_minor = amount_minor
    pricing.currency = currency
    await session.flush()


def _build_snapshot(
    service: Service,
    *,
    free_endpoint_id: int,
    paid_endpoint_id: int,
) -> dict[str, object]:
    return {
        "service": {
            "id": service.id,
            "slug": service.slug,
            "name": service.name,
            "summary": service.summary,
            "lifecycle": service.lifecycle.value,
            "current_change_token": DEMO_CHANGE_TOKEN,
        },
        "endpoints": [
            {
                "id": free_endpoint_id,
                "key": FREE_ENDPOINT_KEY,
                "access_mode": AccessMode.FREE.value,
            },
            {
                "id": paid_endpoint_id,
                "key": PAID_ENDPOINT_KEY,
                "access_mode": AccessMode.PAID.value,
                "pricing": {
                    "pricing_type": PricingModelType.FIXED_PER_CALL.value,
                    "amount_minor": 250,
                    "currency": "USD",
                },
            },
        ],
        "tags": ["demo", "manual-testing"],
    }


async def _ensure_revision(
    session: AsyncSession,
    *,
    service: Service,
    free_endpoint_id: int,
    paid_endpoint_id: int,
) -> None:
    revision = await session.scalar(
        select(ServiceRevision)
        .where(ServiceRevision.service_id == service.id)
        .order_by(ServiceRevision.revision_number.desc()),
    )
    if revision is None:
        revision = ServiceRevision(
            service_id=service.id,
            revision_number=1,
            change_token=DEMO_CHANGE_TOKEN,
            snapshot={},
        )
        session.add(revision)
        await session.flush()

    revision.change_token = DEMO_CHANGE_TOKEN
    revision.snapshot = _build_snapshot(
        service,
        free_endpoint_id=free_endpoint_id,
        paid_endpoint_id=paid_endpoint_id,
    )
    service.current_revision_id = revision.id
    service.current_change_token = revision.change_token
    await session.flush()


async def seed_demo_data() -> SeedResult:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory.begin() as session:
            provider = await _get_or_create_provider(session)
            consumer = await _get_or_create_consumer(session)
            service = await _get_or_create_service(
                session,
                provider_account_id=provider.account_id,
            )
            await _replace_tags(session, service_id=service.id)
            free_endpoint = await _get_or_create_endpoint(
                session,
                service_id=service.id,
                key=FREE_ENDPOINT_KEY,
                access_mode=AccessMode.FREE,
                name="Free Ping",
                summary="A free endpoint for manual invoke testing.",
                timeout_seconds=15,
            )
            paid_endpoint = await _get_or_create_endpoint(
                session,
                service_id=service.id,
                key=PAID_ENDPOINT_KEY,
                access_mode=AccessMode.PAID,
                name="Paid Summary",
                summary="A paid endpoint for quote and x402 testing.",
                timeout_seconds=30,
            )
            await _upsert_upstream(
                session,
                base_url=settings.demo_upstream_base_url,
                endpoint_id=free_endpoint.id,
                path=settings.demo_free_upstream_path,
            )
            await _upsert_upstream(
                session,
                base_url=settings.demo_upstream_base_url,
                endpoint_id=paid_endpoint.id,
                path=settings.demo_paid_upstream_path,
            )
            await _upsert_pricing(
                session,
                endpoint_id=free_endpoint.id,
                pricing_type=PricingModelType.FREE,
                amount_minor=None,
                currency=None,
            )
            await _upsert_pricing(
                session,
                endpoint_id=paid_endpoint.id,
                pricing_type=PricingModelType.FIXED_PER_CALL,
                amount_minor=250,
                currency="USD",
            )
            await _ensure_revision(
                session,
                service=service,
                free_endpoint_id=free_endpoint.id,
                paid_endpoint_id=paid_endpoint.id,
            )
            return SeedResult(
                provider_account_id=provider.account_id,
                consumer_account_id=consumer.account_id,
                service_id=service.id,
                free_endpoint_id=free_endpoint.id,
                paid_endpoint_id=paid_endpoint.id,
            )
    finally:
        await engine.dispose()


async def main() -> None:
    result = await seed_demo_data()
    sys.stdout.write(
        "\n".join(
            [
                f"provider_account_id={result.provider_account_id}",
                f"consumer_account_id={result.consumer_account_id}",
                f"service_id={result.service_id}",
                f"service_slug={DEMO_SERVICE_SLUG}",
                f"free_endpoint_id={result.free_endpoint_id}",
                f"paid_endpoint_id={result.paid_endpoint_id}",
                f"demo_upstream_base_url={get_settings().demo_upstream_base_url}",
                f"demo_free_upstream_path={get_settings().demo_free_upstream_path}",
                f"demo_paid_upstream_path={get_settings().demo_paid_upstream_path}",
            ],
        )
        + "\n",
    )


if __name__ == "__main__":
    asyncio.run(main())
