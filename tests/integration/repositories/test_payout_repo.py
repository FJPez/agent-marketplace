from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import (
    AccessMode,
    InvocationStatus,
    PayoutStatus,
    PricingModelType,
    ServiceLifecycle,
)
from app.db.models import (
    Account,
    Invocation,
    PaymentAttempt,
    Quote,
    Service,
    ServiceEndpoint,
    ServiceRevision,
)
from app.repositories.payout_repo import PayoutRepository


async def _seed_payout_dependencies(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int, int, int, int, int]:
    async with db_session_factory.begin() as session:
        provider = Account(
            display_name="Provider",
            wallet_address="0x00000000000000000000000000000000000000aa",
        )
        consumer = Account(display_name="Consumer")
        session.add_all([provider, consumer])
        await session.flush()

        service = Service(
            provider_account_id=provider.id,
            slug="payout-reporting",
            name="Payout Reporting",
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
            endpoint_key=endpoint.key,
            request_hash="a" * 64,
            pricing_type=PricingModelType.FIXED_PER_CALL,
            amount_minor=500,
            currency="USD",
            service_revision_id=revision.id,
            service_change_token=revision.change_token,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(quote)
        await session.flush()

        invocation = Invocation(
            consumer_account_id=consumer.id,
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key=endpoint.key,
            access_mode=AccessMode.PAID,
            quote_id=quote.id,
            idempotency_key="payout-repo-key",
            request_hash="b" * 64,
            status=InvocationStatus.SUCCEEDED,
            response_payload={"result": "bonjour"},
            upstream_status_code=200,
            error_message=None,
            failure_reason=None,
        )
        session.add(invocation)
        await session.flush()
        retry_invocation = Invocation(
            consumer_account_id=consumer.id,
            service_id=service.id,
            endpoint_id=endpoint.id,
            endpoint_key=endpoint.key,
            access_mode=AccessMode.PAID,
            quote_id=quote.id,
            idempotency_key="payout-repo-key-2",
            request_hash="c" * 64,
            status=InvocationStatus.SUCCEEDED,
            response_payload={"result": "salut"},
            upstream_status_code=200,
            error_message=None,
            failure_reason=None,
        )
        session.add(retry_invocation)
        await session.flush()

        attempt = PaymentAttempt(
            consumer_account_id=consumer.id,
            quote_id=quote.id,
            invocation_id=invocation.id,
            idempotency_key="payout-repo-key",
            payment_identifier="payout-repo-payment",
            payment_requirement={"payment_amount": 5_000_000},
            payment_payload={"payment_identifier": "payout-repo-payment"},
            verify_outcome={"ok": True},
            settle_outcome={"ok": True},
            facilitator_reference="settle-payout-repo",
        )
        session.add(attempt)
        await session.flush()
        retry_attempt = PaymentAttempt(
            consumer_account_id=consumer.id,
            quote_id=quote.id,
            invocation_id=retry_invocation.id,
            idempotency_key="payout-repo-key-2",
            payment_identifier="payout-repo-payment-2",
            payment_requirement={"payment_amount": 5_000_000},
            payment_payload={"payment_identifier": "payout-repo-payment-2"},
            verify_outcome={"ok": True},
            settle_outcome={"ok": True},
            facilitator_reference="settle-payout-repo-2",
        )
        session.add(retry_attempt)
        await session.flush()

        return (
            provider.id,
            service.id,
            invocation.id,
            attempt.id,
            retry_invocation.id,
            retry_attempt.id,
        )


@pytest.mark.asyncio
async def test_payout_repository_persists_lists_and_summarizes_provider_payouts(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    (
        provider_account_id,
        service_id,
        invocation_id,
        payment_attempt_id,
        retry_invocation_id,
        retry_attempt_id,
    ) = await _seed_payout_dependencies(db_session_factory)

    async with db_session_factory.begin() as session:
        repo = PayoutRepository(session)
        first = repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet="0x00000000000000000000000000000000000000aa",
            amount_minor=4_500_000,
            currency="USDC",
            network="base-sepolia",
            status=PayoutStatus.SENT,
        )
        first.transfer_reference = "0xsent"
        second = repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=retry_invocation_id,
            payment_attempt_id=retry_attempt_id,
            destination_wallet="0x00000000000000000000000000000000000000aa",
            amount_minor=4_400_000,
            currency="USDC",
            network="base-sepolia",
            status=PayoutStatus.FAILED,
        )
        second.error_message = "rpc unavailable"

    async with db_session_factory() as session:
        repo = PayoutRepository(session)
        payouts = await repo.list_for_provider(provider_account_id=provider_account_id)
        failed = await repo.list_for_provider(
            provider_account_id=provider_account_id,
            status=PayoutStatus.FAILED,
        )
        summary = await repo.summarize_for_provider(provider_account_id=provider_account_id)

    assert [payout.status.value for payout in payouts] == ["failed", "sent"]
    assert failed[0].error_message == "rpc unavailable"
    assert summary is not None
    assert summary.currency == "USDC"
    assert summary.total_count == 2
    assert summary.sent_count == 1
    assert summary.failed_count == 1
    assert summary.total_amount_minor == 8_900_000
    assert summary.sent_amount_minor == 4_500_000


@pytest.mark.asyncio
async def test_summarize_for_provider_does_not_crash_with_multiple_currencies(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    (
        provider_account_id,
        service_id,
        invocation_id,
        payment_attempt_id,
        retry_invocation_id,
        retry_attempt_id,
    ) = await _seed_payout_dependencies(db_session_factory)

    async with db_session_factory.begin() as session:
        repo = PayoutRepository(session)
        repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet="0x00000000000000000000000000000000000000aa",
            amount_minor=4_500_000,
            currency="USDC",
            network="base-sepolia",
            status=PayoutStatus.SENT,
        )
        repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=retry_invocation_id,
            payment_attempt_id=retry_attempt_id,
            destination_wallet="0x00000000000000000000000000000000000000aa",
            amount_minor=100,
            currency="USDT",
            network="base-sepolia",
            status=PayoutStatus.SENT,
        )

    async with db_session_factory() as session:
        repo = PayoutRepository(session)
        summary = await repo.summarize_for_provider(provider_account_id=provider_account_id)

    assert summary is not None
