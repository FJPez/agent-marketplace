from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    ConsumerAccountFactory,
    EndpointFactory,
    EndpointPriceFactory,
    InvocationFactory,
    PaymentAttemptFactory,
    ProviderAccountFactory,
    QuoteFactory,
    ServiceFactory,
)

from app.core.enums import (
    AccessMode,
    InvocationStatus,
    PaymentAttemptStatus,
    PayoutStatus,
)
from app.repositories.payout_repo import PayoutExecutionRepository


async def _seed_payout_dependencies(
    *,
    provider_account_factory: ProviderAccountFactory,
    consumer_account_factory: ConsumerAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
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
    await endpoint_price_factory(
        endpoint_id=endpoint_id,
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
async def test_payout_execution_repository_persists_and_replays_provider_payouts(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
    provider_account_factory: ProviderAccountFactory,
    consumer_account_factory: ConsumerAccountFactory,
    service_factory: ServiceFactory,
    endpoint_factory: EndpointFactory,
    endpoint_price_factory: EndpointPriceFactory,
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
        endpoint_price_factory=endpoint_price_factory,
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
        execution_repo = PayoutExecutionRepository(session)
        replay = await execution_repo.list_for_provider_request(
            provider_account_id=provider_account_id,
            request_idempotency_key="payout-request-1",
        )
        by_attempt = await execution_repo.get_by_payment_attempt_id(
            payment_attempt_id=retry_attempt_id,
        )
        max_chain_nonce = await execution_repo.get_max_claimed_chain_nonce()

    assert [payout.id for payout in replay] == [first.id, second.id]
    assert [payout.status for payout in replay] == [PayoutStatus.SENT, PayoutStatus.FAILED]
    assert by_attempt is not None
    assert by_attempt.error_message == "rpc unavailable"
    assert max_chain_nonce == 10
