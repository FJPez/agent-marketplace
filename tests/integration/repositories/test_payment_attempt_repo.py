from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import AccessMode, InvocationStatus, PricingModelType, ServiceLifecycle
from app.db.models import (
    Account,
    Invocation,
    PricingModel,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)
from app.repositories.payment_attempt_repo import PaymentAttemptRepository


@pytest.mark.asyncio
async def test_payment_attempt_repository_persists_and_loads_by_identifier(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        provider_account = Account(display_name="Provider")
        consumer_account = Account(display_name="Consumer")
        session.add_all([provider_account, consumer_account])
        await session.flush()

        service = Service(
            provider_account_id=provider_account.id,
            slug="payment-service",
            name="Payment Service",
            summary="Summary",
            description=None,
            lifecycle=ServiceLifecycle.ACTIVE,
        )
        session.add(service)
        await session.flush()
        endpoint = ServiceEndpoint(
            service_id=service.id,
            key="translate",
            name="Translate",
            summary=None,
            description=None,
            access_mode=AccessMode.PAID,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()
        revision = ServiceRevision(
            service_id=service.id,
            revision_number=1,
            change_token="c" * 64,
            snapshot={"slug": service.slug},
        )
        session.add(revision)
        await session.flush()
        quote = Quote(
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key="translate",
            request_hash="a" * 64,
            pricing_type=PricingModelType.FIXED_PER_CALL,
            amount_minor=500,
            currency="USD",
            service_revision_id=revision.id,
            service_change_token=revision.change_token,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(quote)
        session.add(
            PricingModel(
                endpoint_id=endpoint.id,
                pricing_type=PricingModelType.FIXED_PER_CALL,
                amount_minor=500,
                currency="USD",
            )
        )
        await session.flush()
        invocation = Invocation(
            consumer_account_id=consumer_account.id,
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key="translate",
            access_mode=AccessMode.PAID,
            quote_id=quote.id,
            idempotency_key="invoke-key",
            request_hash="a" * 64,
            status=InvocationStatus.SUCCEEDED,
            response_payload={"result": "bonjour"},
            upstream_status_code=200,
            error_message=None,
        )
        session.add(invocation)
        await session.flush()

        repo = PaymentAttemptRepository(session)
        attempt = repo.add(
            consumer_account_id=consumer_account.id,
            quote_id=quote.id,
            invocation_id=invocation.id,
            idempotency_key="invoke-key",
            payment_identifier="payment-1",
            payment_requirement={"amount_minor": 500},
            payment_payload={"payment_identifier": "payment-1"},
            verify_outcome={"ok": True},
            settle_outcome={"ok": True},
            facilitator_reference="settle-1",
        )
        await session.flush()
        attempt_id = attempt.id

    async with db_session_factory() as session:
        repo = PaymentAttemptRepository(session)
        loaded = await repo.get_by_payment_identifier(payment_identifier="payment-1")

    assert loaded is not None
    assert loaded.id == attempt_id
    assert loaded.invocation_id == invocation.id
    assert loaded.facilitator_reference == "settle-1"
