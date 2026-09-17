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
from tests.helpers.x402 import (
    build_payment_requirement,
    build_settle_outcome,
    payment_signature_header,
)

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
from app.integrations.x402.models import (
    PaymentPayload,
    PaymentRequirement,
    SettleOutcome,
    VerifyOutcome,
)
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
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> VerifyOutcome:
        self.calls.append("verify")
        raise AssertionError("a settled attempt must not be verified again")

    async def settle(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> SettleOutcome:
        self.calls.append("settle")
        raise AssertionError("a settled attempt must not be settled again")


class FakeX402ResourceServer:
    def build_payment_required_headers(
        self,
        *,
        requirement: PaymentRequirement,
    ) -> dict[str, str]:
        return {"PAYMENT-REQUIRED": requirement.model_dump_json()}

    def build_payment_response_headers(
        self,
        *,
        outcome: SettleOutcome,
    ) -> dict[str, str]:
        return {"PAYMENT-RESPONSE": outcome.model_dump_json()}


def payment_header() -> str:
    return payment_signature_header(payment_identifier=PAYMENT_IDENTIFIER)


async def test_a_settled_attempt_whose_invocation_already_succeeded_finishes_without_re_forwarding(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
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
                payment_requirement=build_payment_requirement().model_dump(mode="json"),
                payment_payload=PaymentPayload.from_header(payment_header()).wire,
                verify_outcome=VerifyOutcome(accepted=True, checked_by="facilitator").model_dump(
                    mode="json"
                ),
                settle_outcome=build_settle_outcome().model_dump(mode="json"),
                facilitator_reference="0xsettled",
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
