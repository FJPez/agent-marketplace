import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from tests.fixtures.domain import (
    create_endpoint_record,
    create_listing_price_record,
    create_provider_account_record,
    create_service_record,
)
from tests.fixtures.settings import TEST_PRICE_TERMS, TEST_TREASURY_ADDRESS

from app.core.enums import AccessMode
from app.db.errors import unique_violation_constraint
from app.db.models import ListingPrice, Service, ServiceEndpoint
from app.db.models.listing_price import LISTING_PRICE_VERSION_CONSTRAINT


async def _create_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    access_mode: AccessMode = AccessMode.PAID,
) -> int:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(db_session_factory, provider_account_id=account_id)
    return await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        access_mode=access_mode,
    )


async def _create_two_paid_endpoints(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    """Create two paid endpoints of one service, neither priced; return their ids."""
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(db_session_factory, provider_account_id=account_id)
    return (
        await create_endpoint_record(
            db_session_factory,
            service_id=service_id,
            key="translate",
            access_mode=AccessMode.PAID,
        ),
        await create_endpoint_record(
            db_session_factory,
            service_id=service_id,
            key="summarize",
            access_mode=AccessMode.PAID,
        ),
    )


async def test_price_version_stores_its_terms_and_loads_as_the_current_price(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    endpoint_id = await _create_endpoint(db_session_factory)
    await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id)
    second_price_id = await create_listing_price_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount=2**64 + 1,
        version=2,
    )

    async with db_session_factory() as session:
        endpoint = await session.scalar(
            select(ServiceEndpoint)
            .options(selectinload(ServiceEndpoint.current_price))
            .where(ServiceEndpoint.id == endpoint_id),
        )

    assert endpoint is not None
    assert endpoint.current_price is not None
    assert endpoint.current_price.id == second_price_id
    assert endpoint.current_price.version == 2
    assert endpoint.current_price.amount == 2**64 + 1
    assert endpoint.current_price.asset == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    assert endpoint.current_price.network == "eip155:84532"
    assert endpoint.current_price.pay_to == TEST_TREASURY_ADDRESS
    assert endpoint.current_price.max_timeout_seconds == 120
    assert endpoint.current_price.fee_bps == 1_000
    assert endpoint.current_price.created_at is not None


async def test_price_versions_are_unique_per_endpoint_under_a_named_key(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    endpoint_id = await _create_endpoint(db_session_factory)
    await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id)

    with pytest.raises(IntegrityError) as caught:
        await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id)

    assert unique_violation_constraint(caught.value) == LISTING_PRICE_VERSION_CONSTRAINT


async def test_free_endpoint_cannot_have_a_current_price(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    endpoint_id = await _create_endpoint(db_session_factory, access_mode=AccessMode.FREE)

    with pytest.raises(IntegrityError, match="ck_service_endpoints_free_has_no_price"):
        await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("version", 0, "ck_listing_prices_positive_version"),
        ("amount", 0, "ck_listing_prices_positive_amount"),
        ("max_timeout_seconds", 0, "ck_listing_prices_positive_max_timeout_seconds"),
        ("fee_bps", -1, "ck_listing_prices_fee_bps_range"),
        ("fee_bps", 10_001, "ck_listing_prices_fee_bps_range"),
    ],
)
async def test_price_versions_reject_out_of_range_terms(
    db_session_factory: async_sessionmaker[AsyncSession],
    column: str,
    value: int,
    constraint: str,
) -> None:
    endpoint_id = await _create_endpoint(db_session_factory)
    row = {"version": 1, "amount": 10_000} | TEST_PRICE_TERMS | {column: value}

    async with db_session_factory() as session:
        session.add(ListingPrice(endpoint_id=endpoint_id, **row))
        with pytest.raises(IntegrityError, match=constraint):
            await session.flush()


async def test_deleting_a_service_deletes_its_price_versions(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    endpoint_id = await _create_endpoint(db_session_factory)
    await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory.begin() as session:
        await session.execute(delete(Service))

    async with db_session_factory() as session:
        remaining = await session.scalar(select(func.count()).select_from(ListingPrice))

    assert remaining == 0


async def test_current_price_cannot_be_another_endpoints_version(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    endpoint_id, other_endpoint_id = await _create_two_paid_endpoints(db_session_factory)
    price_id = await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id)

    with pytest.raises(
        IntegrityError,
        match="fk_service_endpoints_id_current_price_id_listing_prices",
    ):
        async with db_session_factory.begin() as session:
            await session.execute(
                update(ServiceEndpoint)
                .where(ServiceEndpoint.id == other_endpoint_id)
                .values(current_price_id=price_id),
            )


async def test_current_price_may_be_the_endpoints_own_version_or_none(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    endpoint_id, unpriced_endpoint_id = await _create_two_paid_endpoints(db_session_factory)
    price_id = await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        rows = await session.execute(select(ServiceEndpoint.id, ServiceEndpoint.current_price_id))
        current_price_ids = {row.id: row.current_price_id for row in rows}

    assert current_price_ids == {endpoint_id: price_id, unpriced_endpoint_id: None}


async def test_deleting_an_endpoint_deletes_its_price_versions(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    endpoint_id = await _create_endpoint(db_session_factory)
    await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id)
    await create_listing_price_record(db_session_factory, endpoint_id=endpoint_id, version=2)

    async with db_session_factory.begin() as session:
        await session.execute(delete(ServiceEndpoint).where(ServiceEndpoint.id == endpoint_id))

    async with db_session_factory() as session:
        remaining = await session.scalar(select(func.count()).select_from(ListingPrice))

    assert remaining == 0
