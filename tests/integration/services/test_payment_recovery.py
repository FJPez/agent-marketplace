import json

import pytest
from httpx import Response
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import (
    create_consumer_account_record,
    create_endpoint_price_record,
    create_endpoint_record,
    create_invocation_record,
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
from app.db.models import LedgerEntry, PaymentAttempt, Payout
from app.services import invoke
from app.services.payment_service import PaidInvokeSuccess, PaymentService

pytestmark = [pytest.mark.asyncio]

PAYLOAD: dict[str, object] = {"text": "hello"}
IDEMPOTENCY_KEY = "invoke-key"
PAYMENT_IDENTIFIER = "payment-1"


class FakeHttpClient:
    """Stands in for the outbound http client, the only external I/O in this flow."""

    def __init__(self) -> None:
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
        raise AssertionError("the upstream must not be called again after a settled attempt")

    async def aclose(self) -> None:
        return None


class FakeFacilitatorClient:
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
        raise AssertionError("a settled attempt must not be verified again")

    async def settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        _ = payment_requirement
        _ = payment_payload
        self.calls.append("settle")
        raise AssertionError("a settled attempt must not be settled again")


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


async def test_a_settled_attempt_whose_invocation_already_succeeded_finishes_without_re_forwarding(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    provider_account_id = await create_provider_account_record(db_session_factory)
    consumer_account_id = await create_consumer_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="paid-recovery-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key="translate",
        access_mode=AccessMode.PAID,
    )
    await create_upstream_record(db_session_factory, endpoint_id=endpoint_id)
    await create_endpoint_price_record(
        db_session_factory,
        endpoint_id=endpoint_id,
        amount_minor=500,
        currency="USD",
    )
    quote_id = await create_quote_record(
        db_session_factory,
        service_id=service_id,
        endpoint_id=endpoint_id,
        payload=PAYLOAD,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
    )
    invocation_id = await create_invocation_record(
        db_session_factory,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        access_mode=AccessMode.PAID,
        quote_id=quote_id,
        payload=PAYLOAD,
        idempotency_key=IDEMPOTENCY_KEY,
        status=InvocationStatus.SUCCEEDED,
        response_payload={"result": "bonjour"},
    )
    # The worker died after the invocation succeeded but before the ledger, the payout,
    # and the CONSUMED transition were written.
    async with db_session_factory.begin() as session:
        session.add(
            PaymentAttempt(
                consumer_account_id=consumer_account_id,
                quote_id=quote_id,
                invocation_id=None,
                idempotency_key=IDEMPOTENCY_KEY,
                payment_identifier=PAYMENT_IDENTIFIER,
                status=PaymentAttemptStatus.SETTLED,
                payment_requirement={"amount_minor": 500},
                payment_payload={"payment_identifier": PAYMENT_IDENTIFIER},
                verify_outcome={"ok": True, "reference": "verify-1"},
                settle_outcome={"ok": True, "reference": "settle-1"},
                facilitator_reference="settle-1",
            )
        )

    http_client = FakeHttpClient()
    facilitator_client = FakeFacilitatorClient()
    settings = Settings(treasury_private_key=SecretStr("0x" + "11" * 32))
    async with db_session_factory() as session:
        resolved = await invoke.resolve_target(
            session=session,
            service_ref=service_id,
            endpoint_key="translate",
            payload=PAYLOAD,
            quote_id=quote_id,
        )
        payment_service = PaymentService(
            session,
            http_client=http_client,
            facilitator_client=facilitator_client,
            x402_resource_server=FakeX402ResourceServer(),
            settings=settings,
        )
        result = await payment_service.handle_paid_invoke(
            ActorContext(account_id=consumer_account_id),
            resolved=resolved,
            idempotency_key=IDEMPOTENCY_KEY,
            payment_signature=payment_header(),
        )

    assert isinstance(result, PaidInvokeSuccess)
    assert result.invocation.id == invocation_id
    assert "PAYMENT-RESPONSE" in result.response_headers
    assert http_client.calls == []
    assert facilitator_client.calls == []

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

    assert attempt is not None
    assert attempt.status is PaymentAttemptStatus.CONSUMED
    assert attempt.invocation_id == invocation_id
    assert len(ledger_entries) == 3
    assert len(payouts) == 1
    assert payouts[0].status is PayoutStatus.READY
