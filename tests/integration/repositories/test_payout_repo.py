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

from app.core.enums import (
    AccessMode,
    InvocationStatus,
    PaymentAttemptStatus,
    PayoutStatus,
    PricingModelType,
)
from app.repositories.payout_repo import PayoutExecutionRepository, PayoutReportingRepository


async def _seed_payout_dependencies(
    *,
    provider_account_factory: ProviderAccountFactory,
    consumer_account_factory: ConsumerAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    pricing_factory: PricingFactory,
    quote_factory: QuoteFactory,
    invocation_factory: InvocationFactory,
    payment_attempt_factory: PaymentAttemptFactory,
) -> tuple[int, int, int, int, int, int]:
    provider_account_id = await provider_account_factory(
        display_name="Provider",
        wallet_address="0x00000000000000000000000000000000000000aa",
    )
    consumer_account_id = await consumer_account_factory(display_name="Consumer")
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="payout-reporting",
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
        idempotency_key="payout-repo-key",
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
        upstream_status_code=200,
    )
    retry_invocation_id = await invocation_factory(
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        endpoint_key="translate",
        access_mode=AccessMode.PAID,
        quote_id=quote_id,
        idempotency_key="payout-repo-key-2",
        payload={"text": "retry"},
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "salut"},
        upstream_status_code=200,
    )
    payment_attempt_id = await payment_attempt_factory(
        consumer_account_id=consumer_account_id,
        quote_id=quote_id,
        invocation_id=invocation_id,
        idempotency_key="payout-repo-key",
        payment_identifier="payout-repo-payment",
        status=PaymentAttemptStatus.CONSUMED,
        payment_requirement={"payment_amount": 5_000_000},
        payment_payload={"payment_identifier": "payout-repo-payment"},
        verify_outcome={"ok": True},
        settle_outcome={"ok": True},
        facilitator_reference="settle-payout-repo",
    )
    retry_attempt_id = await payment_attempt_factory(
        consumer_account_id=consumer_account_id,
        quote_id=quote_id,
        invocation_id=retry_invocation_id,
        idempotency_key="payout-repo-key-2",
        payment_identifier="payout-repo-payment-2",
        status=PaymentAttemptStatus.CONSUMED,
        payment_requirement={"payment_amount": 5_000_000},
        payment_payload={"payment_identifier": "payout-repo-payment-2"},
        verify_outcome={"ok": True},
        settle_outcome={"ok": True},
        facilitator_reference="settle-payout-repo-2",
    )

    return (
        provider_account_id,
        service_id,
        invocation_id,
        payment_attempt_id,
        retry_invocation_id,
        retry_attempt_id,
    )


@pytest.mark.asyncio
async def test_payout_repository_persists_lists_and_summarizes_provider_payouts(
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
    (
        provider_account_id,
        service_id,
        invocation_id,
        payment_attempt_id,
        retry_invocation_id,
        retry_attempt_id,
    ) = await _seed_payout_dependencies(
        provider_account_factory=provider_account_factory,
        consumer_account_factory=consumer_account_factory,
        service_factory=service_factory,
        endpoint_factory=endpoint_factory,
        pricing_factory=pricing_factory,
        quote_factory=quote_factory,
        invocation_factory=invocation_factory,
        payment_attempt_factory=payment_attempt_factory,
    )

    async with db_session_factory.begin() as session:
        execution_repo = PayoutExecutionRepository(session)
        first = execution_repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=invocation_id,
            payment_attempt_id=payment_attempt_id,
            destination_wallet="0x00000000000000000000000000000000000000aa",
            amount_minor=4_500_000,
            currency="USDC",
            network="base-sepolia",
            status=PayoutStatus.SENT,
            request_idempotency_key="payout-request-1",
            chain_nonce=9,
        )
        first.transfer_reference = "0xsent"
        second = execution_repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=retry_invocation_id,
            payment_attempt_id=retry_attempt_id,
            destination_wallet="0x00000000000000000000000000000000000000aa",
            amount_minor=4_400_000,
            currency="USDC",
            network="base-sepolia",
            status=PayoutStatus.FAILED,
            request_idempotency_key="payout-request-1",
            chain_nonce=10,
        )
        second.error_message = "rpc unavailable"

    async with db_session_factory() as session:
        reporting_repo = PayoutReportingRepository(session)
        execution_repo = PayoutExecutionRepository(session)
        payouts = await reporting_repo.list_for_provider(provider_account_id=provider_account_id)
        failed = await reporting_repo.list_for_provider(
            provider_account_id=provider_account_id,
            status=PayoutStatus.FAILED,
        )
        replay = await execution_repo.list_for_provider_request(
            provider_account_id=provider_account_id,
            request_idempotency_key="payout-request-1",
        )
        summaries = await reporting_repo.summarize_for_provider(
            provider_account_id=provider_account_id
        )
        max_chain_nonce = await execution_repo.get_max_claimed_chain_nonce()

    assert [payout.status.value for payout in payouts] == ["failed", "sent"]
    assert failed[0].error_message == "rpc unavailable"
    assert [payout.id for payout in replay] == [first.id, second.id]
    assert max_chain_nonce == 10
    assert len(summaries) == 1
    summary = summaries[0]
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
    (
        provider_account_id,
        service_id,
        invocation_id,
        payment_attempt_id,
        retry_invocation_id,
        retry_attempt_id,
    ) = await _seed_payout_dependencies(
        provider_account_factory=provider_account_factory,
        consumer_account_factory=consumer_account_factory,
        service_factory=service_factory,
        endpoint_factory=endpoint_factory,
        pricing_factory=pricing_factory,
        quote_factory=quote_factory,
        invocation_factory=invocation_factory,
        payment_attempt_factory=payment_attempt_factory,
    )

    async with db_session_factory.begin() as session:
        execution_repo = PayoutExecutionRepository(session)
        execution_repo.add(
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
        execution_repo.add(
            provider_account_id=provider_account_id,
            service_id=service_id,
            invocation_id=retry_invocation_id,
            payment_attempt_id=retry_attempt_id,
            destination_wallet="0x00000000000000000000000000000000000000aa",
            amount_minor=100,
            currency="USDC",
            network="base-sepolia",
            status=PayoutStatus.SENT,
        )

    async with db_session_factory() as session:
        reporting_repo = PayoutReportingRepository(session)
        summaries = await reporting_repo.summarize_for_provider(
            provider_account_id=provider_account_id
        )

    assert len(summaries) == 1
    assert summaries[0].currency == "USDC"
    assert summaries[0].total_amount_minor == 4_500_100
