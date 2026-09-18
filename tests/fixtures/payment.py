"""Fakes for the collaborators a paid invoke calls out to: provider, facilitator, x402."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

import pytest
from httpx import Response
from pydantic import SecretStr
from sqlalchemy import select
from tests.fixtures.domain import (
    create_consumer_account_record,
    create_endpoint_price_record,
    create_endpoint_record,
    create_provider_account_record,
    create_quote_record,
    create_service_record,
    create_upstream_record,
)
from tests.helpers.x402 import PAYMENT_ASSET, payment_signature_header

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.enums import AccessMode, PricingModelType
from app.db.models import Invocation, LedgerEntry, PaymentAttempt, Payout
from app.integrations.x402.models import (
    PaymentPayload,
    PaymentRequirement,
    SettleOutcome,
    VerifyOutcome,
)
from app.services import invoke, payment

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.integrations.provider_gateway.client import SupportsRequest
    from app.integrations.x402.protocols import SupportsFacilitatorClient
    from app.services.payment import PaidInvokeSuccess, PaymentRequiredChallenge

type SettleHook = Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]]
type VerifyScript = Sequence[VerifyOutcome | Exception]
type SettleScript = Sequence[SettleOutcome | Exception]

TEST_TREASURY_PRIVATE_KEY = "0x" + "11" * 32
PAID_ENDPOINT_KEY = "translate"
PAID_INVOKE_PAYLOAD: dict[str, object] = {"text": "hello"}


class PaidTarget(NamedTuple):
    """One active paid endpoint with a quote, and the two accounts around it."""

    provider_account_id: int
    consumer_account_id: int
    service_id: int
    endpoint_id: int
    quote_id: int


class NeverCalledHttpClient:
    """Stands in for the outbound provider client where no forward may happen."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object,
        headers: dict[str, str],
        **kwargs: object,
    ) -> Response:
        raise AssertionError("the provider upstream must not be called")

    async def aclose(self) -> None:
        return None


class ScriptedHttpClient:
    """Answers each provider forward with the next scripted response."""

    def __init__(self, *, responses: Sequence[Response] = ()) -> None:
        self.responses = list(responses)
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
        if not self.responses:
            raise AssertionError("no scripted upstream response is left")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return None


class NeverCalledFacilitatorClient:
    """Stands in for the facilitator where neither verify nor settle may happen."""

    async def verify(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> VerifyOutcome:
        raise AssertionError("the facilitator must not be asked to verify")

    async def settle(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> SettleOutcome:
        raise AssertionError("the facilitator must not be asked to settle")


class ScriptedFacilitatorClient:
    """Answers each facilitator call with the next scripted outcome, or raises it."""

    def __init__(
        self,
        *,
        verify_results: VerifyScript = (),
        settle_results: SettleScript = (),
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        on_settle: SettleHook | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.verify_results = list(verify_results)
        self.settle_results = list(settle_results)
        self.on_settle = on_settle
        self.verify_calls: list[str] = []
        self.settle_calls: list[str] = []

    async def verify(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> VerifyOutcome:
        self.verify_calls.append(payload.identifier)
        result = self._take(self.verify_results, "verify")
        if isinstance(result, Exception):
            raise result
        return result

    async def settle(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> SettleOutcome:
        self.settle_calls.append(payload.identifier)
        if self.on_settle is not None:
            assert self.session_factory is not None
            # The hook runs while the settle is in flight, which is the only moment a
            # second session can observe the claim the flow made before this call.
            await self.on_settle(self.session_factory)
        result = self._take(self.settle_results, "settle")
        if isinstance(result, Exception):
            raise result
        return result

    def _take[ResultT](self, results: list[ResultT], operation: str) -> ResultT:
        if not results:
            raise AssertionError(f"no scripted facilitator {operation} result is left")
        return results.pop(0)


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


class MoneyRows(NamedTuple):
    """Everything one settled invocation is supposed to have written about money."""

    ledger_entries: list[LedgerEntry]
    payouts: list[Payout]


class PaymentAttemptLoader(Protocol):
    def __call__(self, *, payment_identifier: str = ...) -> Awaitable[PaymentAttempt]: ...


class PaymentAttemptsLoader(Protocol):
    def __call__(self) -> Awaitable[list[PaymentAttempt]]: ...


class MoneyRowsLoader(Protocol):
    def __call__(self) -> Awaitable[MoneyRows]: ...


class PaidInvokeRunner(Protocol):
    def __call__(
        self,
        *,
        target: PaidTarget,
        facilitator_client: SupportsFacilitatorClient,
        http_client: SupportsRequest | None = ...,
        idempotency_key: str = ...,
        payment_identifier: str = ...,
        asset: str = ...,
        quote_id: int | None = ...,
        account_id: int | None = ...,
    ) -> Coroutine[Any, Any, PaymentRequiredChallenge | PaidInvokeSuccess]: ...


class ReplayedPaidInvokeRunner(Protocol):
    def __call__(
        self,
        *,
        invocation_id: int,
        account_id: int,
    ) -> Coroutine[Any, Any, PaidInvokeSuccess]: ...


class ScriptedFacilitatorFactory(Protocol):
    def __call__(
        self,
        *,
        verify_results: VerifyScript = ...,
        settle_results: SettleScript = ...,
        on_settle: SettleHook | None = ...,
    ) -> ScriptedFacilitatorClient: ...


@pytest.fixture
def never_called_http_client() -> NeverCalledHttpClient:
    return NeverCalledHttpClient()


@pytest.fixture
def never_called_facilitator_client() -> NeverCalledFacilitatorClient:
    return NeverCalledFacilitatorClient()


@pytest.fixture
def fake_x402_resource_server() -> FakeX402ResourceServer:
    return FakeX402ResourceServer()


@pytest.fixture
def scripted_facilitator_client(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> ScriptedFacilitatorFactory:
    def build(
        *,
        verify_results: VerifyScript = (),
        settle_results: SettleScript = (),
        on_settle: SettleHook | None = None,
    ) -> ScriptedFacilitatorClient:
        return ScriptedFacilitatorClient(
            session_factory=db_session_factory,
            verify_results=verify_results,
            settle_results=settle_results,
            on_settle=on_settle,
        )

    return build


@pytest.fixture
def paid_invoke_settings() -> Settings:
    return Settings(treasury_private_key=SecretStr(TEST_TREASURY_PRIVATE_KEY))


@pytest.fixture
async def paid_target(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> PaidTarget:
    provider_account_id = await create_provider_account_record(db_session_factory)
    consumer_account_id = await create_consumer_account_record(db_session_factory)
    service_id = await create_service_record(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="paid-service",
        with_revision=True,
    )
    endpoint_id = await create_endpoint_record(
        db_session_factory,
        service_id=service_id,
        key=PAID_ENDPOINT_KEY,
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
        endpoint_key=PAID_ENDPOINT_KEY,
        payload=PAID_INVOKE_PAYLOAD,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
    )
    return PaidTarget(
        provider_account_id=provider_account_id,
        consumer_account_id=consumer_account_id,
        service_id=service_id,
        endpoint_id=endpoint_id,
        quote_id=quote_id,
    )


@pytest.fixture
def load_payment_attempt(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> PaymentAttemptLoader:
    async def load(*, payment_identifier: str = "payment-1") -> PaymentAttempt:
        async with db_session_factory() as session:
            attempt = await session.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.payment_identifier == payment_identifier,
                ),
            )
        assert attempt is not None
        return attempt

    return load


@pytest.fixture
def load_payment_attempts(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> PaymentAttemptsLoader:
    async def load() -> list[PaymentAttempt]:
        async with db_session_factory() as session:
            attempts = await session.scalars(
                select(PaymentAttempt).order_by(PaymentAttempt.id),
            )
            return list(attempts.all())

    return load


@pytest.fixture
def load_money_rows(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> MoneyRowsLoader:
    async def load() -> MoneyRows:
        async with db_session_factory() as session:
            ledger_entries = list(
                (await session.scalars(select(LedgerEntry).order_by(LedgerEntry.id))).all(),
            )
            payouts = list((await session.scalars(select(Payout).order_by(Payout.id))).all())
        return MoneyRows(ledger_entries=ledger_entries, payouts=payouts)

    return load


@pytest.fixture
def run_paid_invoke(
    db_session_factory: async_sessionmaker[AsyncSession],
    fake_x402_resource_server: FakeX402ResourceServer,
    paid_invoke_settings: Settings,
) -> PaidInvokeRunner:
    async def run(
        *,
        target: PaidTarget,
        facilitator_client: SupportsFacilitatorClient,
        http_client: SupportsRequest | None = None,
        idempotency_key: str = "invoke-key",
        payment_identifier: str = "payment-1",
        asset: str = PAYMENT_ASSET,
        quote_id: int | None = None,
        account_id: int | None = None,
    ) -> PaymentRequiredChallenge | PaidInvokeSuccess:
        # Every call gets its own session, so concurrent callers race the way two
        # workers would rather than sharing one identity map.
        async with db_session_factory() as session:
            resolved = await invoke.resolve_target(
                session=session,
                service_ref=target.service_id,
                endpoint_key=PAID_ENDPOINT_KEY,
                payload=PAID_INVOKE_PAYLOAD,
                quote_id=target.quote_id if quote_id is None else quote_id,
            )
            return await payment.handle_paid_invoke(
                session=session,
                actor=ActorContext(
                    account_id=target.consumer_account_id if account_id is None else account_id,
                ),
                resolved=resolved,
                idempotency_key=idempotency_key,
                payment_signature=payment_signature_header(
                    payment_identifier=payment_identifier,
                    asset=asset,
                ),
                facilitator_client=facilitator_client,
                x402_resource_server=fake_x402_resource_server,
                http_client=NeverCalledHttpClient() if http_client is None else http_client,
                settings=paid_invoke_settings,
            )

    return run


@pytest.fixture
def run_replayed_paid_invoke(
    db_session_factory: async_sessionmaker[AsyncSession],
    fake_x402_resource_server: FakeX402ResourceServer,
    paid_invoke_settings: Settings,
) -> ReplayedPaidInvokeRunner:
    async def run(*, invocation_id: int, account_id: int) -> PaidInvokeSuccess:
        async with db_session_factory() as session:
            invocation = await session.get(Invocation, invocation_id)
            assert invocation is not None
            return await payment.finish_replayed_invocation(
                session=session,
                actor=ActorContext(account_id=account_id),
                invocation=invocation,
                x402_resource_server=fake_x402_resource_server,
                settings=paid_invoke_settings,
            )

    return run
