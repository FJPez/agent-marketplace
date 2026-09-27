from typing import NamedTuple, TypedDict

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_admin_account_record,
    create_endpoint_record,
    create_listing_price_record,
    create_moderation_action_record,
    create_provider_account_record,
    create_service_record,
    create_signing_secret_record,
    create_upstream_record,
)
from tests.fixtures.settings import build_service_settings
from tests.helpers.dns import TEST_UPSTREAM_BASE_URL
from tests.helpers.request_validation import IN_PROCESS_REQUEST_VALIDATION_POOL

from app.core.enums import AccessMode, ServiceLifecycle
from app.core.errors import InvalidStateError, NotFoundError
from app.core.json_types import JsonObject
from app.db.models import ServiceEndpoint
from app.schemas.discovery import PublicServicePricingResponse
from app.schemas.service import EndpointUpdateRequest
from app.services import discovery, listings, moderation, provider_endpoints
from app.services.listings import InvokableListing, ListingPriceTerms, ListingUpstream

# The treasury before a rotation: price versions keep the pay_to they were stamped with.
RETIRED_TREASURY_ADDRESS = "0x2222222222222222222222222222222222222222"
REQUEST_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


class SeededListing(NamedTuple):
    provider_account_id: int
    service_id: int
    endpoint_id: int


class Flaw(TypedDict, total=False):
    """One way a seeded listing falls short of invokable."""

    lifecycle: ServiceLifecycle
    moderation_actions: tuple[str, ...]
    access_mode: AccessMode
    with_upstream: bool
    is_enabled: bool
    with_signing_secret: bool


class Lookup(TypedDict, total=False):
    service_slug: str
    endpoint_key: str


async def _seed_listing(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    lifecycle: ServiceLifecycle = ServiceLifecycle.ACTIVE,
    moderation_actions: tuple[str, ...] = (),
    access_mode: AccessMode = AccessMode.FREE,
    with_upstream: bool = True,
    is_enabled: bool = True,
    with_signing_secret: bool = True,
) -> SeededListing:
    """A listing at /v1/invoke/translator/translate, invokable unless told otherwise."""
    provider_account_id = await create_provider_account_record(db_session_factory)
    if with_signing_secret:
        await create_signing_secret_record(db_session_factory, account_id=provider_account_id)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="translator",
        lifecycle=lifecycle,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=access_mode,
        request_schema=REQUEST_SCHEMA,
        timeout_seconds=20,
        is_enabled=is_enabled,
    )
    if with_upstream:
        await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)
    for action in moderation_actions:
        await create_moderation_action_record(
            db_session_factory,
            service_id=service_id,
            action=action,
        )
    return SeededListing(provider_account_id, service_id, endpoint_id)


async def _load(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_slug: str = "translator",
    endpoint_key: str = "translate",
) -> InvokableListing:
    async with db_session_factory() as session:
        return await listings.load_invokable_listing(
            session=session,
            service_slug=service_slug,
            endpoint_key=endpoint_key,
        )


async def test_a_free_listing_loads_with_what_the_invoke_path_needs(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_listing(db_session_factory)

    listing = await _load(db_session_factory)

    assert listing == InvokableListing(
        listing_id=seeded.endpoint_id,
        service_id=seeded.service_id,
        provider_account_id=seeded.provider_account_id,
        upstream=ListingUpstream(
            base_url=TEST_UPSTREAM_BASE_URL,
            path="/invoke",
            http_method="POST",
        ),
        price=None,
        timeout_seconds=20,
        supports_idempotency=False,
        response_content_type="application/json",
        request_schema=REQUEST_SCHEMA,
    )


async def test_a_paid_listing_loads_its_current_price_version_as_stored(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_listing(db_session_factory, access_mode=AccessMode.PAID)
    await create_listing_price_record(db_session_factory, endpoint_id=seeded.endpoint_id)
    # Stamped with a treasury the settings no longer name: loaded as it is.
    current_price_id = await create_listing_price_record(
        db_session_factory,
        endpoint_id=seeded.endpoint_id,
        amount=40_000,
        version=2,
        pay_to=RETIRED_TREASURY_ADDRESS,
    )

    listing = await _load(db_session_factory)

    assert listing.price == ListingPriceTerms(
        id=current_price_id,
        version=2,
        amount=40_000,
        asset="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        network="eip155:84532",
        pay_to=RETIRED_TREASURY_ADDRESS,
        max_timeout_seconds=120,
        fee_bps=1_000,
    )


@pytest.mark.parametrize(
    ("flaw", "lookup"),
    [
        ({"lifecycle": ServiceLifecycle.DRAFT}, {}),
        ({"moderation_actions": ("suspend",)}, {}),
        ({"moderation_actions": ("delist",)}, {}),
        ({"is_enabled": False}, {}),
        ({"with_upstream": False}, {}),
        ({"access_mode": AccessMode.PAID}, {}),
        ({"with_signing_secret": False}, {}),
        ({}, {"service_slug": "unknown"}),
        ({}, {"endpoint_key": "unknown"}),
    ],
    ids=[
        "draft_service",
        "suspended_service",
        "delisted_service",
        "disabled_endpoint",
        "no_upstream",
        "paid_without_a_price",
        "no_signing_secret",
        "unknown_service",
        "unknown_endpoint",
    ],
)
async def test_a_listing_that_cannot_be_invoked_is_not_found(
    db_session_factory: async_sessionmaker[AsyncSession],
    flaw: Flaw,
    lookup: Lookup,
) -> None:
    await _seed_listing(db_session_factory, **flaw)

    with pytest.raises(NotFoundError, match="listing not found"):
        await _load(db_session_factory, **lookup)


async def test_an_endpoint_is_found_only_under_its_own_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_listing(db_session_factory)
    other_service_id = await create_service_record(
        db_session_factory,
        provider_account_id=seeded.provider_account_id,
        slug="summarizer",
    )
    other_endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=other_service_id,
        key="summarize",
    )
    await create_upstream_record(db_session_factory, endpoint_id=other_endpoint_id)

    with pytest.raises(NotFoundError):
        await _load(db_session_factory, endpoint_key="summarize")
    other = await _load(db_session_factory, service_slug="summarizer", endpoint_key="summarize")
    assert other.listing_id == other_endpoint_id


async def test_a_suspension_after_a_load_hides_the_listing_from_the_next_load(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_listing(db_session_factory)
    admin_account_id = await create_admin_account_record(db_session_factory)
    loaded = await _load(db_session_factory)

    async with db_session_factory() as session:
        await moderation.suspend_service(
            session=session,
            service_id=seeded.service_id,
            actor_account_id=admin_account_id,
            reason="abuse report",
        )

    assert loaded.listing_id == seeded.endpoint_id
    with pytest.raises(NotFoundError):
        await _load(db_session_factory)


async def test_a_restored_service_loads_again(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_listing(db_session_factory)
    admin_account_id = await create_admin_account_record(db_session_factory)

    async with db_session_factory() as session:
        await moderation.suspend_service(
            session=session,
            service_id=seeded.service_id,
            actor_account_id=admin_account_id,
            reason="abuse report",
        )
    with pytest.raises(NotFoundError):
        await _load(db_session_factory)

    async with db_session_factory() as session:
        await moderation.restore_service(
            session=session,
            service_id=seeded.service_id,
            actor_account_id=admin_account_id,
            reason="reviewed",
        )

    listing = await _load(db_session_factory)
    assert listing.listing_id == seeded.endpoint_id


async def test_a_loaded_price_can_be_checked_against_the_current_terms(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """is_on_current_terms takes a ListingPrice today; phase 1 will call it with the
    loader's own ListingPriceTerms, so the two must be interchangeable for it."""
    seeded = await _seed_listing(db_session_factory, access_mode=AccessMode.PAID)
    await create_listing_price_record(db_session_factory, endpoint_id=seeded.endpoint_id)

    listing = await _load(db_session_factory)

    assert listing.price is not None
    assert provider_endpoints.is_on_current_terms(listing.price, settings=build_service_settings())


async def test_a_session_with_an_open_transaction_is_refused_without_side_effects(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A caller mid-transaction gets a programming-error signal, not a silently
    discarded commit: the loader used to end the caller's transaction with the
    rollback it uses to end its own read.
    """
    seeded = await _seed_listing(db_session_factory)

    async with db_session_factory() as session:
        endpoint = await session.get(ServiceEndpoint, seeded.endpoint_id)
        assert endpoint is not None
        endpoint.timeout_seconds = 25
        await session.flush()
        assert session.in_transaction()

        with pytest.raises(RuntimeError, match="no transaction open"):
            await listings.load_invokable_listing(
                session=session,
                service_slug="translator",
                endpoint_key="translate",
            )
        assert session.in_transaction()
        await session.commit()

    async with db_session_factory() as session:
        endpoint = await session.get(ServiceEndpoint, seeded.endpoint_id)
        assert endpoint is not None
        assert endpoint.timeout_seconds == 25


async def test_loading_ends_its_read_transaction(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_listing(db_session_factory)

    async with db_session_factory() as session:
        await listings.load_invokable_listing(
            session=session,
            service_slug="translator",
            endpoint_key="translate",
        )
        assert not session.in_transaction()

        with pytest.raises(NotFoundError):
            await listings.load_invokable_listing(
                session=session,
                service_slug="translator",
                endpoint_key="unknown",
            )
        assert not session.in_transaction()


async def test_every_endpoint_discovery_lists_can_be_loaded(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_listing(db_session_factory)
    paid_id = await create_endpoint_record(
        db_session_factory,
        service_id=seeded.service_id,
        key="summarize",
        access_mode=AccessMode.PAID,
    )
    await create_upstream_record(db_session_factory, endpoint_id=paid_id)
    await create_listing_price_record(db_session_factory, endpoint_id=paid_id)
    unfinished_id = await create_endpoint_record(
        db_session_factory,
        service_id=seeded.service_id,
        key="unfinished",
        is_enabled=False,
    )
    async with db_session_factory() as session:
        # The one way an endpoint without an upstream could reach discovery.
        with pytest.raises(InvalidStateError):
            await provider_endpoints.update_endpoint(
                session=session,
                settings=build_service_settings(),
                validation_pool=IN_PROCESS_REQUEST_VALIDATION_POOL,
                account_id=seeded.provider_account_id,
                endpoint_id=unfinished_id,
                changes=EndpointUpdateRequest(is_enabled=True),
            )

    async with db_session_factory() as session:
        service = await discovery.get_service(session=session, service_ref="translator")
        listed = PublicServicePricingResponse.from_model(service).endpoints

    assert sorted(endpoint.key for endpoint in listed) == ["summarize", "translate"]
    for endpoint in listed:
        loaded = await _load(db_session_factory, endpoint_key=endpoint.key)
        assert loaded.service_id == seeded.service_id
