"""Provider editing of endpoint prices: immutable versions, minimum, treasury, revisions."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_record,
    create_listing_price_record,
    create_provider_account_record,
    create_service_record,
    create_trusted_provider_records,
    create_upstream_record,
    read_price_versions,
)
from tests.fixtures.settings import TEST_TREASURY_ADDRESS, build_service_settings
from tests.helpers.dns import FakeResolver
from tests.helpers.request_validation import INLINE_REQUEST_VALIDATION_POOL

from app.core.config import Settings
from app.core.enums import AccessMode, ServiceLifecycle
from app.core.errors import InvalidInputError, InvalidStateError
from app.db.models import ListingPrice, ServiceEndpoint, ServiceRevision
from app.schemas.pricing import ListingPriceRequest
from app.schemas.service import EndpointCreateRequest, EndpointUpdateRequest
from app.services import publishing
from app.services.provider_endpoints import create_endpoint, update_endpoint


def _create_request(*, access_mode: AccessMode, amount: int | None) -> EndpointCreateRequest:
    return EndpointCreateRequest(
        key="translate",
        name="Translate",
        access_mode=access_mode,
        request_schema={"type": "object"},
        response_schema={"type": "object"},
        timeout_seconds=30,
        price=None if amount is None else ListingPriceRequest(amount=amount),
    )


async def _create_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    lifecycle: ServiceLifecycle = ServiceLifecycle.DRAFT,
    access_mode: AccessMode = AccessMode.PAID,
    price_amount: int | None = None,
) -> tuple[int, int]:
    """Seed an owned endpoint, optionally with a first price version; return the ids."""
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        lifecycle=lifecycle,
        with_revision=lifecycle is ServiceLifecycle.ACTIVE,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=access_mode,
    )
    if price_amount is not None:
        await create_listing_price_record(
            db_session_factory,
            endpoint_id=endpoint_id,
            amount=price_amount,
        )
    return account_id, endpoint_id


async def _update(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
    endpoint_id: int,
    changes: EndpointUpdateRequest,
    settings: Settings | None = None,
) -> ServiceEndpoint:
    async with db_session_factory() as session:
        return await update_endpoint(
            session=session,
            settings=settings or build_service_settings(),
            validation_pool=INLINE_REQUEST_VALIDATION_POOL,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=changes,
        )


async def _revision_count(
    db_session_factory: async_sessionmaker[AsyncSession],
    endpoint_id: int,
) -> int:
    async with db_session_factory() as session:
        endpoint = await session.get(ServiceEndpoint, endpoint_id)
        assert endpoint is not None
        revisions = await session.scalars(
            select(ServiceRevision.id).where(ServiceRevision.service_id == endpoint.service_id),
        )
        return len(revisions.all())


async def test_create_endpoint_stores_a_first_price_version_with_the_current_terms(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        lifecycle=ServiceLifecycle.DRAFT,
    )
    settings = build_service_settings().model_copy(update={"platform_fee_bps": 250})

    async with db_session_factory() as session:
        endpoint = await create_endpoint(
            session=session,
            settings=settings,
            validation_pool=INLINE_REQUEST_VALIDATION_POOL,
            account_id=account_id,
            service_id=service_id,
            request=_create_request(access_mode=AccessMode.PAID, amount=10_000),
        )

    async with db_session_factory() as session:
        price = await session.get(ListingPrice, endpoint.current_price_id)

    assert price is not None
    assert price.endpoint_id == endpoint.id
    assert price.version == 1
    assert price.amount == 10_000
    assert price.asset == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    assert price.network == "eip155:84532"
    assert price.pay_to == TEST_TREASURY_ADDRESS
    assert price.max_timeout_seconds == 120
    assert price.fee_bps == 250


@pytest.mark.parametrize("access_mode", [AccessMode.FREE, AccessMode.PAID])
async def test_create_endpoint_without_a_price_stores_no_version(
    db_session_factory: async_sessionmaker[AsyncSession],
    access_mode: AccessMode,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        endpoint = await create_endpoint(
            session=session,
            settings=build_service_settings(),
            validation_pool=INLINE_REQUEST_VALIDATION_POOL,
            account_id=account_id,
            service_id=service_id,
            request=_create_request(access_mode=access_mode, amount=None),
        )

    assert await read_price_versions(db_session_factory, endpoint_id=endpoint.id) == (None, [])


async def test_create_endpoint_below_the_minimum_price_creates_nothing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        lifecycle=ServiceLifecycle.DRAFT,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match="at least 10000 atomic units"):
            await create_endpoint(
                session=session,
                settings=build_service_settings(),
                validation_pool=INLINE_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                service_id=service_id,
                request=_create_request(access_mode=AccessMode.PAID, amount=9_999),
            )
        await session.commit()

    async with db_session_factory() as session:
        endpoints = await session.scalars(select(ServiceEndpoint.id))
        assert endpoints.all() == []


async def test_price_one_unit_below_the_minimum_is_rejected_and_stores_nothing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match="at least 10000 atomic units"):
            await update_endpoint(
                session=session,
                settings=build_service_settings(),
                validation_pool=INLINE_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(
                    timeout_seconds=20,
                    price=ListingPriceRequest(amount=9_999),
                ),
            )
        await session.commit()

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
    assert persisted is not None
    assert persisted.timeout_seconds == 30
    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (None, [])


async def test_price_version_needs_a_treasury_address(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="APP_TREASURY_ADDRESS"):
            await update_endpoint(
                session=session,
                settings=build_service_settings().model_copy(update={"treasury_address": None}),
                validation_pool=INLINE_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(
                    timeout_seconds=20,
                    price=ListingPriceRequest(amount=10_000),
                ),
            )
        await session.commit()

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
    assert persisted is not None
    assert persisted.timeout_seconds == 30
    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (None, [])


async def test_price_edit_creates_a_new_version_and_keeps_the_old_one(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory, price_amount=250_000)

    updated = await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(price=ListingPriceRequest(amount=99_999)),
    )

    assert updated.current_price is not None
    assert updated.current_price.version == 2
    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        2,
        [(1, 250_000), (2, 99_999)],
    )


async def test_price_edit_repeating_the_current_amount_is_a_no_op(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory, price_amount=250_000)
    async with db_session_factory() as session:
        seeded = await session.get(ServiceEndpoint, endpoint_id)
        assert seeded is not None
        seeded_updated_at = seeded.updated_at

    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(price=ListingPriceRequest(amount=250_000)),
    )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
    assert persisted is not None
    assert persisted.updated_at == seeded_updated_at
    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        1,
        [(1, 250_000)],
    )


@pytest.mark.parametrize(
    ("setting", "term", "value"),
    [
        ("treasury_address", "pay_to", "0x2222222222222222222222222222222222222222"),
        ("platform_fee_bps", "fee_bps", 250),
        ("payment_network", "network", "eip155:8453"),
        ("payment_asset", "asset", "0x3333333333333333333333333333333333333333"),
        ("payment_max_timeout_seconds", "max_timeout_seconds", 300),
    ],
    ids=["treasury", "fee", "network", "asset", "window"],
)
async def test_resending_the_price_moves_it_onto_changed_payment_terms(
    db_session_factory: async_sessionmaker[AsyncSession],
    setting: str,
    term: str,
    value: str | int,
) -> None:
    account_id, endpoint_id = await _create_endpoint(
        db_session_factory,
        lifecycle=ServiceLifecycle.ACTIVE,
        price_amount=250_000,
    )

    updated = await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(price=ListingPriceRequest(amount=250_000)),
        settings=build_service_settings().model_copy(update={setting: value}),
    )

    assert updated.current_price is not None
    assert getattr(updated.current_price, term) == value
    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        2,
        [(1, 250_000), (2, 250_000)],
    )
    assert await _revision_count(db_session_factory, endpoint_id) == 2


async def test_resending_the_current_amount_without_a_treasury_is_a_no_op(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory, price_amount=250_000)
    async with db_session_factory() as session:
        seeded = await session.get(ServiceEndpoint, endpoint_id)
        assert seeded is not None
        seeded_updated_at = seeded.updated_at

    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(price=ListingPriceRequest(amount=250_000)),
        settings=build_service_settings().model_copy(update={"treasury_address": None}),
    )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
    assert persisted is not None
    assert persisted.updated_at == seeded_updated_at
    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        1,
        [(1, 250_000)],
    )


async def test_omitting_the_price_keeps_the_current_version(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory, price_amount=250_000)

    # Even when the payment terms have changed since the version was created.
    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(timeout_seconds=20),
        settings=build_service_settings().model_copy(update={"platform_fee_bps": 250}),
    )

    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        1,
        [(1, 250_000)],
    )


async def test_clearing_a_draft_price_keeps_its_versions(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory, price_amount=250_000)

    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(price=None),
    )

    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        None,
        [(1, 250_000)],
    )


async def test_paid_to_free_to_paid_keeps_old_versions_and_numbers_the_new_one_next(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory, price_amount=250_000)

    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(access_mode=AccessMode.FREE),
    )
    free_state = await read_price_versions(db_session_factory, endpoint_id=endpoint_id)
    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(access_mode=AccessMode.PAID),
    )
    unpriced_state = await read_price_versions(db_session_factory, endpoint_id=endpoint_id)
    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(price=ListingPriceRequest(amount=40_000)),
    )

    assert free_state == (None, [(1, 250_000)])
    assert unpriced_state == (None, [(1, 250_000)])
    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        2,
        [(1, 250_000), (2, 40_000)],
    )


async def test_price_on_a_free_endpoint_is_rejected(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(
        db_session_factory,
        access_mode=AccessMode.FREE,
    )

    with pytest.raises(InvalidInputError, match="free endpoints cannot have a price"):
        await _update(
            db_session_factory,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(price=ListingPriceRequest(amount=10_000)),
        )


@pytest.mark.parametrize("access_mode", [AccessMode.FREE, AccessMode.PAID])
async def test_null_price_without_a_current_price_is_a_no_op(
    db_session_factory: async_sessionmaker[AsyncSession],
    access_mode: AccessMode,
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory, access_mode=access_mode)
    async with db_session_factory() as session:
        seeded = await session.get(ServiceEndpoint, endpoint_id)
        assert seeded is not None
        seeded_updated_at = seeded.updated_at

    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(price=None),
    )

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
    assert persisted is not None
    assert persisted.updated_at == seeded_updated_at


async def test_active_paid_endpoint_rejects_clearing_its_price_without_mutating_state(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(
        db_session_factory,
        lifecycle=ServiceLifecycle.ACTIVE,
        price_amount=250_000,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match="active paid endpoints must define a price"):
            await update_endpoint(
                session=session,
                settings=build_service_settings(),
                validation_pool=INLINE_REQUEST_VALIDATION_POOL,
                account_id=account_id,
                endpoint_id=endpoint_id,
                changes=EndpointUpdateRequest(timeout_seconds=20, price=None),
            )
        await session.commit()

    async with db_session_factory() as session:
        persisted = await session.get(ServiceEndpoint, endpoint_id)
    assert persisted is not None
    assert persisted.timeout_seconds == 30
    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        1,
        [(1, 250_000)],
    )
    assert await _revision_count(db_session_factory, endpoint_id) == 1


async def test_active_endpoint_rejects_turning_paid_without_a_price(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(
        db_session_factory,
        lifecycle=ServiceLifecycle.ACTIVE,
        access_mode=AccessMode.FREE,
    )

    with pytest.raises(InvalidInputError, match="active paid endpoints must define a price"):
        await _update(
            db_session_factory,
            account_id=account_id,
            endpoint_id=endpoint_id,
            changes=EndpointUpdateRequest(access_mode=AccessMode.PAID),
        )


async def test_active_free_to_paid_with_a_price_creates_a_version_and_a_revision(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id, endpoint_id = await _create_endpoint(
        db_session_factory,
        lifecycle=ServiceLifecycle.ACTIVE,
        access_mode=AccessMode.FREE,
    )

    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(
            access_mode=AccessMode.PAID,
            price=ListingPriceRequest(amount=10_000),
        ),
    )

    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        1,
        [(1, 10_000)],
    )
    assert await _revision_count(db_session_factory, endpoint_id) == 2


@pytest.mark.parametrize("price_amount", [250_000, None], ids=["priced", "unpriced"])
async def test_active_paid_to_free_clears_the_current_price_and_revises(
    db_session_factory: async_sessionmaker[AsyncSession],
    price_amount: int | None,
) -> None:
    account_id, endpoint_id = await _create_endpoint(
        db_session_factory,
        lifecycle=ServiceLifecycle.ACTIVE,
        price_amount=price_amount,
    )

    await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(access_mode=AccessMode.FREE),
    )

    current_version, _ = await read_price_versions(db_session_factory, endpoint_id=endpoint_id)
    assert current_version is None
    assert await _revision_count(db_session_factory, endpoint_id) == 2


async def test_price_change_on_a_published_service_revises_and_keeps_the_old_revision(
    db_session_factory: async_sessionmaker[AsyncSession],
    dns_resolver: FakeResolver,
) -> None:
    account_id, endpoint_id = await _create_endpoint(db_session_factory, price_amount=250_000)
    await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)
    await create_trusted_provider_records(db_session_factory, account_id=account_id)
    async with db_session_factory() as session:
        endpoint = await session.get(ServiceEndpoint, endpoint_id)
        assert endpoint is not None
        service_id = endpoint.service_id
        first_price_id = endpoint.current_price_id
    async with db_session_factory() as session:
        await publishing.publish_service(
            session=session,
            resolver=dns_resolver,
            account_id=account_id,
            service_id=service_id,
        )

    updated = await _update(
        db_session_factory,
        account_id=account_id,
        endpoint_id=endpoint_id,
        changes=EndpointUpdateRequest(price=ListingPriceRequest(amount=500_000)),
    )

    async with db_session_factory() as session:
        stored_revisions = await session.scalars(
            select(ServiceRevision)
            .where(ServiceRevision.service_id == service_id)
            .order_by(ServiceRevision.revision_number),
        )
        snapshots = [revision.snapshot["endpoints"] for revision in stored_revisions]
    endpoint_snapshot = {
        "id": endpoint_id,
        "key": "translate",
        "access_mode": "paid",
        "request_schema": {"type": "object"},
        "response_schema": {"type": "object"},
        "response_content_type": "application/json",
        "timeout_seconds": 30,
        "supports_idempotency": False,
        "is_enabled": True,
    }
    assert updated.current_price is not None
    assert snapshots == [
        [endpoint_snapshot | {"price": {"id": first_price_id, "version": 1}}],
        [endpoint_snapshot | {"price": {"id": updated.current_price.id, "version": 2}}],
    ]
    assert await read_price_versions(db_session_factory, endpoint_id=endpoint_id) == (
        2,
        [(1, 250_000), (2, 500_000)],
    )
