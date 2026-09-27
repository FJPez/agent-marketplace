from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from eth_account import Account as EthAccount
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.enums import AccessMode, AppEnv, ServiceLifecycle
from app.db.models import (
    Account,
    ListingPrice,
    ProviderSigningSecret,
    ProviderUpstream,
    Service,
    ServiceEndpoint,
    ServiceRevision,
    ServiceTag,
)
from app.db.session import create_engine, create_session_factory
from app.services import provider_endpoints, provider_signing_secrets

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DEMO_PROVIDER_NAME = "Demo Provider"
DEMO_SERVICE_SLUG = "demo-agent-service"
DEMO_SERVICE_NAME = "Demo Agent Service"
DEMO_SERVICE_SUMMARY = "Free and paid demo endpoints for manual marketplace testing."
DEMO_SERVICE_DESCRIPTION = (
    "A seeded service for manual testing of discovery, publishing, and pricing flows."
)
DEMO_CHANGE_TOKEN = "d" * 64
FREE_ENDPOINT_KEY = "free-ping"
PAID_ENDPOINT_KEY = "paid-summary"
# 0.25 USDC in atomic units.
PAID_ENDPOINT_AMOUNT = 250_000


@dataclass(frozen=True, slots=True)
class SeedResult:
    provider_account_id: int
    service_id: int
    free_endpoint_id: int
    paid_endpoint_id: int
    provider_wallet_address: str
    consumer_wallet_address: str | None
    provider_signing_secret: str


def _get_required_private_key(env_name: str, *, purpose: str) -> str:
    private_key = os.getenv(env_name, "").strip()
    if private_key:
        return private_key
    raise RuntimeError(f"{env_name} is required for {purpose}")


def _wallet_address_from_private_key(private_key: str, *, env_name: str) -> str:
    try:
        return EthAccount.from_key(private_key).address
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{env_name} is not a valid EVM private key") from exc


def _resolve_demo_wallets() -> tuple[str, str | None]:
    provider_wallet_address = _wallet_address_from_private_key(
        _get_required_private_key(
            "PROVIDER_PRIVATE_KEY",
            purpose="seeding the demo provider account",
        ),
        env_name="PROVIDER_PRIVATE_KEY",
    )
    consumer_private_key = os.getenv("CONSUMER_PRIVATE_KEY", "").strip()
    if not consumer_private_key:
        return provider_wallet_address, None

    consumer_wallet_address = _wallet_address_from_private_key(
        consumer_private_key,
        env_name="CONSUMER_PRIVATE_KEY",
    )
    if provider_wallet_address.lower() == consumer_wallet_address.lower():
        raise RuntimeError(
            "PROVIDER_PRIVATE_KEY and CONSUMER_PRIVATE_KEY must resolve to different wallets",
        )
    return provider_wallet_address, consumer_wallet_address


async def _get_or_create_account(
    session: AsyncSession,
    *,
    wallet_address: str,
    display_name: str,
) -> Account:
    account = await session.scalar(
        select(Account).where(Account.wallet_address == wallet_address),
    )
    if account is None:
        account = Account(wallet_address=wallet_address, display_name=display_name)
        session.add(account)
        await session.flush()

    account.display_name = display_name
    await session.flush()
    return account


async def _get_or_create_service(session: AsyncSession, *, provider_account_id: int) -> Service:
    service = await session.scalar(select(Service).where(Service.slug == DEMO_SERVICE_SLUG))
    if service is None:
        service = Service(
            provider_account_id=provider_account_id,
            slug=DEMO_SERVICE_SLUG,
            name=DEMO_SERVICE_NAME,
            summary=DEMO_SERVICE_SUMMARY,
            description=DEMO_SERVICE_DESCRIPTION,
            lifecycle=ServiceLifecycle.ACTIVE,
        )
        session.add(service)
        await session.flush()

    service.provider_account_id = provider_account_id
    service.name = DEMO_SERVICE_NAME
    service.summary = DEMO_SERVICE_SUMMARY
    service.description = DEMO_SERVICE_DESCRIPTION
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
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            response_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
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
        )
        session.add(upstream)

    upstream.base_url = base_url
    upstream.path = path
    upstream.http_method = "POST"
    await session.flush()


async def _ensure_paid_price(
    session: AsyncSession,
    *,
    settings: Settings,
    endpoint: ServiceEndpoint,
) -> ListingPrice:
    """Keep the paid endpoint on sale at PAID_ENDPOINT_AMOUNT on the current payment terms.

    A new version is added when the amount or any term differs. Without a treasury
    the service refuses to build one (InvalidStateError).
    """
    if endpoint.current_price_id is not None:
        current = await session.get(ListingPrice, endpoint.current_price_id)
        if (
            current is not None
            and current.amount == PAID_ENDPOINT_AMOUNT
            and provider_endpoints.is_on_current_terms(current, settings=settings)
        ):
            return current

    price = provider_endpoints.build_price_version(settings=settings, amount=PAID_ENDPOINT_AMOUNT)
    await provider_endpoints.put_price_on_sale(session=session, endpoint=endpoint, price=price)
    return price


async def _ensure_signing_secret(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    account_id: int,
) -> str:
    """Give the demo provider a signing secret if it lacks one; return its current secret.

    Phase 1 signs every forwarded request with a provider's secret, so a demo listing
    seeded without one would only fail after a consumer paid. Uses its own short
    session rather than the caller's transaction, matching how
    `provider_signing_secrets` commits its own work. Raises InvalidStateError, naming
    APP_PROVIDER_SECRET_ENCRYPTION_KEYS, when that setting is not configured.
    """
    async with session_factory() as session:
        existing = await session.get(ProviderSigningSecret, account_id)
        if existing is None:
            _, secret = await provider_signing_secrets.create_signing_secret(
                session=session,
                settings=settings,
                account_id=account_id,
            )
            return secret

        signing_secrets = await provider_signing_secrets.load_signing_secrets(
            session=session,
            settings=settings,
            account_id=account_id,
        )
        return signing_secrets[0]


def _build_snapshot(
    service: Service,
    *,
    free_endpoint_id: int,
    paid_endpoint_id: int,
    paid_price: ListingPrice,
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
                "price": {"id": paid_price.id, "version": paid_price.version},
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
    paid_price: ListingPrice,
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
        paid_price=paid_price,
    )
    service.current_revision_id = revision.id
    service.current_change_token = revision.change_token
    await session.flush()


async def seed_demo_data() -> SeedResult:
    settings = get_settings()
    # It creates a signing secret that main() prints, so never in a deployed environment.
    if settings.env not in {AppEnv.DEV, AppEnv.TEST}:
        msg = f"the demo seed runs only in dev and test, not {settings.env}"
        raise RuntimeError(msg)
    provider_wallet_address, consumer_wallet_address = _resolve_demo_wallets()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory.begin() as session:
            provider = await _get_or_create_account(
                session,
                wallet_address=provider_wallet_address,
                display_name=DEMO_PROVIDER_NAME,
            )
            service = await _get_or_create_service(
                session,
                provider_account_id=provider.id,
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
                summary="A paid endpoint for pricing and discovery testing.",
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
            paid_price = await _ensure_paid_price(
                session,
                settings=settings,
                endpoint=paid_endpoint,
            )
            await _ensure_revision(
                session,
                service=service,
                free_endpoint_id=free_endpoint.id,
                paid_endpoint_id=paid_endpoint.id,
                paid_price=paid_price,
            )
            provider_account_id = provider.id
            service_id = service.id
            free_endpoint_id = free_endpoint.id
            paid_endpoint_id = paid_endpoint.id

        # Its own short transaction, after the one above commits: phase 1 must sign
        # every forward, so the demo listings need this before they can load.
        provider_signing_secret = await _ensure_signing_secret(
            session_factory,
            settings=settings,
            account_id=provider_account_id,
        )
        return SeedResult(
            provider_account_id=provider_account_id,
            service_id=service_id,
            free_endpoint_id=free_endpoint_id,
            paid_endpoint_id=paid_endpoint_id,
            provider_wallet_address=provider_wallet_address,
            consumer_wallet_address=consumer_wallet_address,
            provider_signing_secret=provider_signing_secret,
        )
    finally:
        await engine.dispose()


async def main() -> None:
    result = await seed_demo_data()
    sys.stdout.write(
        "\n".join(
            [
                f"provider_account_id={result.provider_account_id}",
                f"service_id={result.service_id}",
                f"service_slug={DEMO_SERVICE_SLUG}",
                f"free_endpoint_id={result.free_endpoint_id}",
                f"paid_endpoint_id={result.paid_endpoint_id}",
                f"demo_upstream_base_url={get_settings().demo_upstream_base_url}",
                f"demo_free_upstream_path={get_settings().demo_free_upstream_path}",
                f"demo_paid_upstream_path={get_settings().demo_paid_upstream_path}",
                f"demo_provider_wallet={result.provider_wallet_address}",
                f"demo_provider_signing_secret={result.provider_signing_secret}",
            ],
        )
        + "\n",
    )
    if result.consumer_wallet_address is not None:
        sys.stdout.write(f"configured_consumer_wallet={result.consumer_wallet_address}\n")


if __name__ == "__main__":
    asyncio.run(main())
