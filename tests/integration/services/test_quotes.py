from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_endpoint_price_record,
    create_endpoint_record,
    create_moderation_action_record,
    create_provider_account_record,
    create_quote_record,
    create_revision_record,
    create_service_record,
)

from app.core.config import get_settings
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.core.errors import InvalidStateError, NotFoundError
from app.core.request_hash import hash_request_body
from app.db.models import Quote, Service, ServiceEndpoint
from app.schemas.quote import QuoteCreateRequest
from app.services import quotes

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]


async def test_create_quote_snapshots_price_and_contract_binding(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency="USD",
    )
    settings = get_settings()

    async with db_session_factory() as session:
        quote = await quotes.create_quote(
            session=session,
            settings=settings,
            service_ref="quote-service",
            request=QuoteCreateRequest(endpoint_key="translate", payload={"text": "hello"}),
        )
        quote_id = quote.id

    async with db_session_factory() as session:
        persisted = await session.get(Quote, quote_id)
        service = await session.get(Service, service_id)

    assert persisted is not None
    assert service is not None
    assert persisted.service_id == service_id
    assert persisted.endpoint_id == endpoint_id
    assert persisted.endpoint_key == "translate"
    assert persisted.pricing_type is PricingModelType.FIXED_PER_CALL
    assert persisted.amount_minor == 500
    assert persisted.currency == "USD"
    assert persisted.request_hash == hash_request_body({"text": "hello"})
    assert persisted.service_revision_id == service.current_revision_id
    assert persisted.service_change_token == service.current_change_token
    assert persisted.expires_at - persisted.created_at == timedelta(
        seconds=settings.quote_ttl_seconds,
    )


async def test_create_quote_rejects_a_service_that_is_not_active(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="draft-service",
        lifecycle=ServiceLifecycle.DRAFT,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="service not found"):
            await quotes.create_quote(
                session=session,
                settings=get_settings(),
                service_ref="draft-service",
                request=QuoteCreateRequest(endpoint_key="translate", payload={"text": "hello"}),
            )


async def test_create_quote_hides_a_suspended_service_behind_not_found(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="suspended-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    await create_moderation_action_record(
        db_session_factory,
        service_id=service_id,
        action="suspend",
    )

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="service not found"):
            await quotes.create_quote(
                session=session,
                settings=get_settings(),
                service_ref=service_id,
                request=QuoteCreateRequest(endpoint_key="translate", payload={"text": "hello"}),
            )


async def test_create_quote_rejects_a_service_without_a_contract_binding(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="unbound-service",
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="service contract is not quoteable"):
            await quotes.create_quote(
                session=session,
                settings=get_settings(),
                service_ref=service_id,
                request=QuoteCreateRequest(endpoint_key="translate", payload={"text": "hello"}),
            )


async def test_create_quote_treats_a_disabled_endpoint_as_missing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
        is_enabled=False,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="endpoint not found"):
            await quotes.create_quote(
                session=session,
                settings=get_settings(),
                service_ref=service_id,
                request=QuoteCreateRequest(endpoint_key="translate", payload={"text": "hello"}),
            )


async def test_create_quote_rejects_a_free_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="free-service",
        with_revision=True,
    )
    await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.FREE,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="endpoint is not quoteable"):
            await quotes.create_quote(
                session=session,
                settings=get_settings(),
                service_ref=service_id,
                request=QuoteCreateRequest(endpoint_key="translate", payload={"text": "hello"}),
            )


async def test_create_quote_rejects_a_paid_endpoint_without_a_price(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="unpriced-service",
        with_revision=True,
    )
    await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="endpoint is not quoteable"):
            await quotes.create_quote(
                session=session,
                settings=get_settings(),
                service_ref=service_id,
                request=QuoteCreateRequest(endpoint_key="translate", payload={"text": "hello"}),
            )


async def test_validate_quote_returns_the_quote_for_an_unchanged_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )

    async with db_session_factory() as session:
        quote = await quotes.validate_quote(
            session=session,
            quote_id=quote_id,
            payload={"text": "hello"},
        )

    assert quote.id == quote_id


async def test_validate_quote_rejects_an_unknown_quote(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="quote not found"):
            await quotes.validate_quote(session=session, quote_id=1, payload={"text": "hello"})


async def test_validate_quote_rejects_an_expired_quote(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    async with db_session_factory() as session:
        with pytest.raises(quotes.QuoteExpiredError, match="quote has expired"):
            await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload={"text": "hello"},
            )


async def test_validate_quote_rejects_a_payload_that_does_not_match_the_quote(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )

    async with db_session_factory() as session:
        with pytest.raises(quotes.QuoteMismatchError, match="request hash does not match quote"):
            await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload={"text": "goodbye"},
            )


async def test_validate_quote_rejects_a_service_that_left_the_active_catalogue(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )

    async with db_session_factory.begin() as session:
        service = await session.get(Service, service_id)
        assert service is not None
        service.lifecycle = ServiceLifecycle.DRAFT

    async with db_session_factory() as session:
        with pytest.raises(
            quotes.QuoteStaleError,
            match="quote no longer matches current service state",
        ):
            await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload={"text": "hello"},
            )


async def test_validate_quote_rejects_a_suspended_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    await create_moderation_action_record(
        db_session_factory,
        service_id=service_id,
        action="suspend",
    )

    async with db_session_factory() as session:
        with pytest.raises(
            quotes.QuoteStaleError,
            match="quote no longer matches current service state",
        ):
            await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload={"text": "hello"},
            )


async def test_validate_quote_rejects_an_endpoint_that_was_disabled(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )

    async with db_session_factory.begin() as session:
        endpoint = await session.get(ServiceEndpoint, endpoint_id)
        assert endpoint is not None
        endpoint.is_enabled = False

    async with db_session_factory() as session:
        with pytest.raises(
            quotes.QuoteStaleError,
            match="quote no longer matches current service state",
        ):
            await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload={"text": "hello"},
            )


async def test_validate_quote_rejects_a_quote_bound_to_a_superseded_revision(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )
    await create_revision_record(
        db_session_factory,
        service_id=service_id,
        revision_number=2,
        change_token="d" * 64,
    )

    async with db_session_factory() as session:
        with pytest.raises(
            quotes.QuoteStaleError,
            match="quote no longer matches current service state",
        ):
            await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload={"text": "hello"},
            )


async def test_validate_quote_prefers_expiry_over_a_payload_mismatch(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    async with db_session_factory() as session:
        with pytest.raises(quotes.QuoteExpiredError, match="quote has expired"):
            await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload={"text": "goodbye"},
            )


async def test_validate_quote_prefers_a_payload_mismatch_over_a_stale_service(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )

    async with db_session_factory.begin() as session:
        service = await session.get(Service, service_id)
        assert service is not None
        service.lifecycle = ServiceLifecycle.DRAFT

    async with db_session_factory() as session:
        with pytest.raises(quotes.QuoteMismatchError, match="request hash does not match quote"):
            await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload={"text": "goodbye"},
            )


async def test_validate_quote_rejects_a_service_whose_contract_binding_was_cleared(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=account_id,
        slug="quote-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_endpoint_price_record(db_session_factory, endpoint_id=endpoint_id)
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload={"text": "hello"},
    )

    async with db_session_factory.begin() as session:
        service = await session.get(Service, service_id)
        assert service is not None
        service.current_revision_id = None
        service.current_change_token = None

    async with db_session_factory() as session:
        with pytest.raises(
            quotes.QuoteStaleError,
            match="quote no longer matches current service state",
        ):
            await quotes.validate_quote(
                session=session,
                quote_id=quote_id,
                payload={"text": "hello"},
            )
