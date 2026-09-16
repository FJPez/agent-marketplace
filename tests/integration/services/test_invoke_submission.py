import json
from dataclasses import dataclass

import pytest
from httpx import Response
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_consumer_account_record,
    create_endpoint_price_record,
    create_endpoint_record,
    create_invocation_record,
    create_payment_attempt_record,
    create_provider_account_record,
    create_quote_record,
    create_service_record,
    create_upstream_record,
)
from x402 import PaymentPayload
from x402.http import encode_payment_signature_header

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.enums import (
    AccessMode,
    InvocationStatus,
    PaymentAttemptStatus,
    PayoutStatus,
    PricingModelType,
)
from app.db.models import Invocation, LedgerEntry, PaymentAttempt, Payout
from app.schemas.invoke import InvokeRequest
from app.services.invoke_submission import InvokeSuccess, submit
from app.services.payment_service import PaymentRequiredChallenge

pytestmark = [pytest.mark.asyncio]

PAYLOAD: dict[str, object] = {"text": "hello"}
IDEMPOTENCY_KEY = "submit-key"
PAYMENT_IDENTIFIER = "payment-1"


class FakeHttpClient:
    """Stands in for the outbound http client, the submit path's only provider I/O."""

    def __init__(self, responses: list[Response] | None = None) -> None:
        self.responses: list[Response] = [] if responses is None else responses
        self.calls: list[str] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object,
        headers: dict[str, str],
        **kwargs: object,
    ) -> Response:
        _ = json
        _ = headers
        _ = kwargs
        self.calls.append(f"{method} {url}")
        if not self.responses:
            raise AssertionError("no fake upstream response configured")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return None


class FakeFacilitatorClient:
    """Fails loudly: no case here may reach the facilitator."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def verify(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        _ = payment_requirement
        _ = payment_payload
        self.calls.append("verify")
        raise AssertionError("the facilitator must not be called")

    async def settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        _ = payment_requirement
        _ = payment_payload
        self.calls.append("settle")
        raise AssertionError("the facilitator must not be called")


class FakeX402ResourceServer:
    def build_payment_required_headers(
        self,
        *,
        payment_requirement: dict[str, object],
    ) -> dict[str, str]:
        return {"PAYMENT-REQUIRED": json.dumps(payment_requirement, sort_keys=True)}

    def build_payment_response_headers(
        self,
        *,
        settle_outcome: dict[str, object],
    ) -> dict[str, str]:
        return {"PAYMENT-RESPONSE": json.dumps(settle_outcome, sort_keys=True)}


@dataclass(frozen=True, slots=True)
class SubmitTarget:
    consumer_account_id: int
    service_id: int
    endpoint_id: int


def payment_header() -> str:
    return encode_payment_signature_header(
        PaymentPayload.model_validate(
            {
                "payload": {
                    "authorization": {"nonce": PAYMENT_IDENTIFIER},
                    "transaction": "0xabc123",
                },
                "accepted": {
                    "scheme": "exact",
                    "network": "eip155:84532",
                    "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                    "amount": "500",
                    "payTo": "0x000000000000000000000000000000000000c0de",
                    "maxTimeoutSeconds": 300,
                    "extra": {},
                },
            }
        )
    )


async def seed_target(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    access_mode: AccessMode = AccessMode.FREE,
) -> SubmitTarget:
    provider_account_id = await create_provider_account_record(db_session_factory)
    consumer_account_id = await create_consumer_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug=slug,
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=access_mode,
    )
    await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)
    if access_mode is AccessMode.PAID:
        await create_endpoint_price_record(
            db_session_factory,
            endpoint_id=endpoint_id,
            amount_minor=500,
            currency="USD",
        )
    return SubmitTarget(
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
    )


async def seed_paid_quote(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    target: SubmitTarget,
) -> int:
    return await create_quote_record(
        db_session_factory,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        payload=PAYLOAD,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
    )


async def run_submit(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    target: SubmitTarget,
    http_client: FakeHttpClient,
    facilitator_client: FakeFacilitatorClient,
    quote_id: int | None = None,
    payment_signature: str | None = None,
    idempotency_key: str = IDEMPOTENCY_KEY,
) -> InvokeSuccess | PaymentRequiredChallenge:
    async with db_session_factory() as session:
        return await submit(
            session=session,
            actor=ActorContext(account_id=target.consumer_account_id),
            service_ref=target.service_id,
            request=InvokeRequest(
                endpoint_key="translate",
                payload=PAYLOAD,
                quote_id=quote_id,
            ),
            idempotency_key=idempotency_key,
            payment_signature=payment_signature,
            http_client=http_client,
            facilitator_client=facilitator_client,
            x402_resource_server=FakeX402ResourceServer(),
            settings=Settings(treasury_private_key=SecretStr("0x" + "11" * 32)),
        )


async def read_invocation(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    idempotency_key: str = IDEMPOTENCY_KEY,
) -> Invocation | None:
    async with db_session_factory() as session:
        return await session.scalar(
            select(Invocation).where(Invocation.idempotency_key == idempotency_key),
        )


async def count_invocations(db_session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with db_session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Invocation))
    assert count is not None
    return count


async def test_free_invoke_succeeds_and_stores_the_invocation(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="submit-free")
    http_client = FakeHttpClient([Response(status_code=200, json={"result": "bonjour"})])
    facilitator_client = FakeFacilitatorClient()

    outcome = await run_submit(
        db_session_factory,
        target=target,
        http_client=http_client,
        facilitator_client=facilitator_client,
    )

    persisted = await read_invocation(db_session_factory)

    assert isinstance(outcome, InvokeSuccess)
    assert outcome.response_headers == {}
    assert persisted is not None
    assert outcome.invocation.id == persisted.id
    assert persisted.status is InvocationStatus.SUCCEEDED
    assert persisted.response_payload == {"result": "bonjour"}
    assert persisted.in_progress_until is None
    assert len(http_client.calls) == 1


async def test_free_replay_returns_the_stored_invocation_without_a_second_forward(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(db_session_factory, slug="submit-free-replay")
    http_client = FakeHttpClient([Response(status_code=200, json={"result": "bonjour"})])
    facilitator_client = FakeFacilitatorClient()

    first = await run_submit(
        db_session_factory,
        target=target,
        http_client=http_client,
        facilitator_client=facilitator_client,
    )
    second = await run_submit(
        db_session_factory,
        target=target,
        http_client=http_client,
        facilitator_client=facilitator_client,
    )

    assert isinstance(first, InvokeSuccess)
    assert isinstance(second, InvokeSuccess)
    assert second.invocation.id == first.invocation.id
    assert second.response_headers == {}
    assert len(http_client.calls) == 1
    assert await count_invocations(db_session_factory) == 1


async def test_paid_replay_of_a_consumed_attempt_returns_the_settlement_headers(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(
        db_session_factory,
        slug="submit-paid-replay",
        access_mode=AccessMode.PAID,
    )
    quote_id = await seed_paid_quote(db_session_factory, target=target)
    invocation_id = await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        access_mode=AccessMode.PAID,
        quote_id=quote_id,
        payload=PAYLOAD,
        idempotency_key=IDEMPOTENCY_KEY,
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
    )
    await create_payment_attempt_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        quote_id=quote_id,
        invocation_id=invocation_id,
        idempotency_key=IDEMPOTENCY_KEY,
        payment_identifier=PAYMENT_IDENTIFIER,
        status=PaymentAttemptStatus.CONSUMED,
        verify_outcome={"ok": True, "reference": "verify-1"},
        settle_outcome={"ok": True, "reference": "settle-1"},
    )
    http_client = FakeHttpClient()
    facilitator_client = FakeFacilitatorClient()

    outcome = await run_submit(
        db_session_factory,
        target=target,
        http_client=http_client,
        facilitator_client=facilitator_client,
        quote_id=quote_id,
        payment_signature=payment_header(),
    )

    assert isinstance(outcome, InvokeSuccess)
    assert outcome.invocation.id == invocation_id
    assert "PAYMENT-RESPONSE" in outcome.response_headers
    assert http_client.calls == []
    assert facilitator_client.calls == []


async def test_paid_replay_of_a_settled_attempt_finishes_the_accounting_without_re_forwarding(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(
        db_session_factory,
        slug="submit-paid-settled",
        access_mode=AccessMode.PAID,
    )
    quote_id = await seed_paid_quote(db_session_factory, target=target)
    invocation_id = await create_invocation_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        service_id=target.service_id,
        endpoint_id=target.endpoint_id,
        access_mode=AccessMode.PAID,
        quote_id=quote_id,
        payload=PAYLOAD,
        idempotency_key=IDEMPOTENCY_KEY,
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
    )
    # The worker died after the invocation succeeded but before the ledger, the payout,
    # and the CONSUMED transition were written, so the attempt is not linked to it yet.
    await create_payment_attempt_record(
        db_session_factory,
        consumer_account_id=target.consumer_account_id,
        quote_id=quote_id,
        invocation_id=None,
        idempotency_key=IDEMPOTENCY_KEY,
        payment_identifier=PAYMENT_IDENTIFIER,
        status=PaymentAttemptStatus.SETTLED,
        verify_outcome={"ok": True, "reference": "verify-1"},
        settle_outcome={"ok": True, "reference": "settle-1"},
        facilitator_reference="settle-1",
    )
    http_client = FakeHttpClient()
    facilitator_client = FakeFacilitatorClient()

    outcome = await run_submit(
        db_session_factory,
        target=target,
        http_client=http_client,
        facilitator_client=facilitator_client,
        quote_id=quote_id,
        payment_signature=payment_header(),
    )

    async with db_session_factory() as session:
        attempt = await session.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.payment_identifier == PAYMENT_IDENTIFIER,
            ),
        )
        ledger_entries = list(
            (await session.scalars(select(LedgerEntry).order_by(LedgerEntry.id))).all(),
        )
        payouts = list((await session.scalars(select(Payout).order_by(Payout.id))).all())

    assert isinstance(outcome, InvokeSuccess)
    assert outcome.invocation.id == invocation_id
    assert "PAYMENT-RESPONSE" in outcome.response_headers
    assert attempt is not None
    assert attempt.status is PaymentAttemptStatus.CONSUMED
    assert attempt.invocation_id == invocation_id
    assert len(ledger_entries) == 3
    assert len(payouts) == 1
    assert payouts[0].status is PayoutStatus.READY
    assert http_client.calls == []
    assert facilitator_client.calls == []


async def test_paid_invoke_without_a_payment_signature_challenges_and_stores_nothing(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await seed_target(
        db_session_factory,
        slug="submit-paid-challenge",
        access_mode=AccessMode.PAID,
    )
    quote_id = await seed_paid_quote(db_session_factory, target=target)
    http_client = FakeHttpClient()
    facilitator_client = FakeFacilitatorClient()

    outcome = await run_submit(
        db_session_factory,
        target=target,
        http_client=http_client,
        facilitator_client=facilitator_client,
        quote_id=quote_id,
    )

    assert isinstance(outcome, PaymentRequiredChallenge)
    assert "PAYMENT-REQUIRED" in outcome.headers
    assert outcome.body == {"detail": "payment required"}
    assert http_client.calls == []
    assert facilitator_client.calls == []
    assert await count_invocations(db_session_factory) == 0
