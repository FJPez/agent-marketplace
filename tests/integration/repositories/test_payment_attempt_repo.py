from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    ConsumerAccountFactory,
    EndpointFactory,
    InvocationFactory,
    PaymentAttemptFactory,
    PricingFactory,
    ProviderAccountFactory,
    QuoteFactory,
    ServiceFactory,
)

from app.core.enums import AccessMode, InvocationStatus, PaymentAttemptStatus, PricingModelType
from app.db.models import PaymentAttempt
from app.repositories.payment_attempt_repo import PaymentAttemptRepository


@pytest.mark.asyncio
async def test_payment_attempt_repository_persists_and_loads_by_identifier(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    provider_account_factory: ProviderAccountFactory,
    consumer_account_factory: ConsumerAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    pricing_factory: PricingFactory,
    quote_factory: QuoteFactory,
    invocation_factory: InvocationFactory,
    payment_attempt_factory: PaymentAttemptFactory,
) -> None:
    _ = migrated_database
    _ = db_session_factory

    provider_account_id = await provider_account_factory(display_name="Provider")
    consumer_account_id = await consumer_account_factory(display_name="Consumer")
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="payment-service",
        with_revision=True,
    )
    endpoint_id = await endpoint_factory(
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await pricing_factory(
        endpoint_id=endpoint_id,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
    )
    quote_id = await quote_factory(
        service_id=service_id,
        endpoint_id=endpoint_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    invocation_id = await invocation_factory(
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        endpoint_key="translate",
        access_mode=AccessMode.PAID,
        quote_id=quote_id,
        idempotency_key="invoke-key",
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
        upstream_status_code=200,
    )
    await payment_attempt_factory(
        consumer_account_id=consumer_account_id,
        quote_id=quote_id,
        invocation_id=invocation_id,
        idempotency_key="invoke-key",
        payment_identifier="payment-1",
        status=PaymentAttemptStatus.CONSUMED,
        payment_requirement={"amount_minor": 500},
        payment_payload={"payment_identifier": "payment-1"},
        verify_outcome={"ok": True},
        settle_outcome={"ok": True},
        facilitator_reference="settle-1",
    )

    async with db_session_factory() as session:
        repo = PaymentAttemptRepository(session)
        loaded = await repo.get_by_payment_identifier(payment_identifier="payment-1")

    assert loaded is not None
    assert isinstance(loaded, PaymentAttempt)
    assert loaded.invocation_id == invocation_id
    assert loaded.status is PaymentAttemptStatus.CONSUMED
    assert loaded.facilitator_reference == "settle-1"
    assert loaded.updated_at >= loaded.created_at
