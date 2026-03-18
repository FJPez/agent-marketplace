from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.exc import IntegrityError
from x402 import PaymentPayload
from x402.http import encode_payment_signature_header

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.enums import AccessMode, PaymentAttemptStatus, PricingModelType, ServiceLifecycle
from app.db.models import Quote, Service, ServiceEndpoint
from app.integrations.provider_gateway.signing import HmacAuthConfig
from app.integrations.x402.facilitator_client import FacilitatorAuthError
from app.services import payment_service as payment_service_module
from app.services.invoke_service import (
    InvokeBadGatewayError,
    ResolvedInvokeTarget,
)
from app.services.payment_service import PaidInvokeSuccess, PaymentService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.integrations.provider_gateway.client import SupportsRequest
    from app.services.payment_service import SupportsFacilitatorClient, SupportsX402ResourceServer


class FakeSession:
    def __init__(self, *, raise_integrity_on_commit: bool = False) -> None:
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.raise_integrity_on_commit = raise_integrity_on_commit

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.raise_integrity_on_commit and self.commit_calls == 1:
            raise IntegrityError("statement", {}, Exception("duplicate payment identifier"))

    async def rollback(self) -> None:
        self.rollback_calls += 1


class FakeCommitSequenceSession:
    def __init__(
        self,
        *,
        fail_on_commit_calls: set[int] | None = None,
        on_successful_commit: Callable[[], None] | None = None,
    ) -> None:
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.fail_on_commit_calls = fail_on_commit_calls or set()
        self.on_successful_commit = on_successful_commit

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_calls in self.fail_on_commit_calls:
            raise RuntimeError("commit failed")
        if self.on_successful_commit is not None:
            self.on_successful_commit()

    async def rollback(self) -> None:
        self.rollback_calls += 1


@dataclass
class FakeInvocation:
    id: int


@dataclass
class FakeAttempt:
    id: int
    invocation_id: int | None
    quote_id: int
    idempotency_key: str
    status: PaymentAttemptStatus
    settle_outcome: dict[str, object] | None
    verify_outcome: dict[str, object] | None = None
    facilitator_reference: str | None = None


class FakePaymentAttemptRepository:
    def __init__(self, *, existing_attempt: FakeAttempt | None = None) -> None:
        self.add_calls = 0
        self.existing_attempt = existing_attempt
        self.added_attempt: FakeAttempt | None = None

    async def get_by_payment_identifier(self, *, payment_identifier: str) -> FakeAttempt | None:
        _ = payment_identifier
        return self.existing_attempt

    async def get_by_invocation_id(self, *, invocation_id: int) -> FakeAttempt | None:
        if self.added_attempt is not None and self.added_attempt.invocation_id == invocation_id:
            return self.added_attempt
        existing = self.existing_attempt
        if existing is not None and existing.invocation_id == invocation_id:
            return existing
        return None

    def add(
        self,
        *,
        consumer_account_id: int,
        quote_id: int,
        invocation_id: int | None,
        idempotency_key: str,
        payment_identifier: str | None,
        status: PaymentAttemptStatus,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
        verify_outcome: dict[str, object] | None,
        settle_outcome: dict[str, object] | None,
        facilitator_reference: str | None,
    ) -> FakeAttempt:
        _ = consumer_account_id
        _ = idempotency_key
        _ = payment_identifier
        _ = payment_requirement
        _ = payment_payload
        self.add_calls += 1
        attempt = FakeAttempt(
            id=41,
            invocation_id=invocation_id,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            status=status,
            settle_outcome=settle_outcome,
            verify_outcome=verify_outcome,
            facilitator_reference=facilitator_reference,
        )
        self.added_attempt = attempt
        return attempt


class FakePersistedPaymentAttemptRepository:
    def __init__(self) -> None:
        self.add_calls = 0
        self.working_attempt: FakeAttempt | None = None
        self.stored_attempt: FakeAttempt | None = None

    async def get_by_payment_identifier(self, *, payment_identifier: str) -> FakeAttempt | None:
        _ = payment_identifier
        return self.stored_attempt

    async def get_by_invocation_id(self, *, invocation_id: int) -> FakeAttempt | None:
        if self.stored_attempt is not None and self.stored_attempt.invocation_id == invocation_id:
            return self.stored_attempt
        return None

    def add(
        self,
        *,
        consumer_account_id: int,
        quote_id: int,
        invocation_id: int | None,
        idempotency_key: str,
        payment_identifier: str | None,
        status: PaymentAttemptStatus,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
        verify_outcome: dict[str, object] | None,
        settle_outcome: dict[str, object] | None,
        facilitator_reference: str | None,
    ) -> FakeAttempt:
        _ = consumer_account_id
        _ = payment_identifier
        _ = payment_requirement
        _ = payment_payload
        self.add_calls += 1
        self.working_attempt = FakeAttempt(
            id=41,
            invocation_id=invocation_id,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            status=status,
            settle_outcome=settle_outcome,
            verify_outcome=verify_outcome,
            facilitator_reference=facilitator_reference,
        )
        self.stored_attempt = FakeAttempt(
            id=41,
            invocation_id=invocation_id,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            status=status,
            settle_outcome=settle_outcome,
            verify_outcome=verify_outcome,
            facilitator_reference=facilitator_reference,
        )
        return self.working_attempt

    def persist(self) -> None:
        assert self.working_attempt is not None
        assert self.stored_attempt is not None
        self.stored_attempt.invocation_id = self.working_attempt.invocation_id
        self.stored_attempt.status = self.working_attempt.status
        self.stored_attempt.settle_outcome = self.working_attempt.settle_outcome
        self.stored_attempt.verify_outcome = self.working_attempt.verify_outcome
        self.stored_attempt.facilitator_reference = self.working_attempt.facilitator_reference


class FakeInvokeService:
    def __init__(
        self,
        *,
        replayable_invocation: FakeInvocation | None = None,
        invocation_for_lookup: FakeInvocation | None = None,
        execute_exception: Exception | None = None,
    ) -> None:
        self.replayable_invocation = replayable_invocation
        self.invocation_for_lookup = invocation_for_lookup
        self.execute_exception = execute_exception
        self.get_replayable_invocation_calls = 0
        self.execute_calls = 0
        self.get_invocation_calls = 0
        self.get_invocation_by_idempotency_calls = 0

    async def get_replayable_invocation(
        self,
        actor: ActorContext,
        *,
        idempotency_key: str,
        request_hash: str,
    ) -> FakeInvocation | None:
        _ = actor
        _ = idempotency_key
        _ = request_hash
        self.get_replayable_invocation_calls += 1
        return self.replayable_invocation

    async def execute(
        self,
        actor: ActorContext,
        *,
        resolved: ResolvedInvokeTarget,
        idempotency_key: str,
    ) -> FakeInvocation:
        _ = actor
        _ = resolved
        _ = idempotency_key
        self.execute_calls += 1
        if self.execute_exception is not None:
            raise self.execute_exception
        return FakeInvocation(id=88)

    async def get_invocation(
        self,
        actor: ActorContext,
        *,
        invocation_id: int,
    ) -> FakeInvocation:
        _ = actor
        self.get_invocation_calls += 1
        assert self.invocation_for_lookup is not None
        assert invocation_id == self.invocation_for_lookup.id
        return self.invocation_for_lookup

    async def get_invocation_by_idempotency_key(
        self,
        actor: ActorContext,
        *,
        idempotency_key: str,
    ) -> FakeInvocation | None:
        _ = actor
        _ = idempotency_key
        self.get_invocation_by_idempotency_calls += 1
        return self.invocation_for_lookup


class FakeIdempotentInvokeService(FakeInvokeService):
    def __init__(self) -> None:
        super().__init__()
        self.side_effect_idempotency_keys: set[str] = set()

    async def execute(
        self,
        actor: ActorContext,
        *,
        resolved: ResolvedInvokeTarget,
        idempotency_key: str,
    ) -> FakeInvocation:
        _ = actor
        _ = resolved
        self.execute_calls += 1
        self.side_effect_idempotency_keys.add(idempotency_key)
        return FakeInvocation(id=88)


class FakeFacilitatorClient:
    def __init__(
        self,
        *,
        verify_outcomes: list[dict[str, object]] | None = None,
        settle_outcomes: list[dict[str, object]] | None = None,
    ) -> None:
        self.verify_calls = 0
        self.settle_calls = 0
        default_verify_outcomes: list[dict[str, object]] = [{"ok": True, "reference": "verify-1"}]
        default_settle_outcomes: list[dict[str, object]] = [{"ok": True, "reference": "settle-1"}]
        self.verify_outcomes = (
            verify_outcomes if verify_outcomes is not None else default_verify_outcomes
        )
        self.settle_outcomes = (
            settle_outcomes if settle_outcomes is not None else default_settle_outcomes
        )

    async def verify(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        _ = payment_requirement
        _ = payment_payload
        self.verify_calls += 1
        return self.verify_outcomes.pop(0)

    async def settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        _ = payment_requirement
        _ = payment_payload
        self.settle_calls += 1
        return self.settle_outcomes.pop(0)


class FakeX402ResourceServer:
    def build_payment_required_headers(
        self,
        *,
        payment_requirement: dict[str, object],
    ) -> dict[str, str]:
        _ = payment_requirement
        return {}

    def build_payment_response_headers(
        self,
        *,
        settle_outcome: dict[str, object],
    ) -> dict[str, str]:
        return {"PAYMENT-RESPONSE": str(settle_outcome.get("reference", ""))}


class FakeHttpClient:
    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object,
        headers: dict[str, str],
        **kwargs: object,
    ) -> object:
        _ = method
        _ = url
        _ = json
        _ = headers
        _ = kwargs
        msg = "http client should not be used in payment unit tests"
        raise AssertionError(msg)

    async def aclose(self) -> None:
        return None


class FakeLedgerService:
    def __init__(self) -> None:
        self.record_calls: list[dict[str, object]] = []

    async def record_paid_invocation(self, **kwargs: object) -> None:
        self.record_calls.append(kwargs)


def _payment_header(*, payment_identifier: str) -> str:
    return encode_payment_signature_header(
        PaymentPayload.model_validate(
            {
                "payload": {
                    "authorization": {"nonce": payment_identifier},
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


def _resolved_target() -> ResolvedInvokeTarget:
    quote = Quote(
        id=21,
        service_id=101,
        endpoint_id=303,
        endpoint_key="translate",
        request_hash="a" * 64,
        pricing_type=PricingModelType.FIXED_PER_CALL,
        amount_minor=500,
        currency="USD",
        service_revision_id=1,
        service_change_token="c" * 64,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    service = Service(
        id=101,
        provider_account_id=1,
        slug="invoke-service",
        name="Invoke Service",
        summary="Summary",
        description=None,
        lifecycle=ServiceLifecycle.ACTIVE,
    )
    endpoint = ServiceEndpoint(
        id=303,
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
    return ResolvedInvokeTarget(
        service=service,
        endpoint=endpoint,
        request_hash="a" * 64,
        quote=quote,
        auth=HmacAuthConfig(key_id="gateway-key", secret="super-secret"),
        payload={"text": "hello"},
    )


def _build_service(
    session: FakeSession | FakeCommitSequenceSession,
    *,
    facilitator_client: FakeFacilitatorClient | None = None,
) -> PaymentService:
    return PaymentService(
        cast("AsyncSession", session),
        http_client=cast("SupportsRequest", FakeHttpClient()),
        facilitator_client=cast(
            "SupportsFacilitatorClient",
            facilitator_client or FakeFacilitatorClient(),
        ),
        x402_resource_server=cast("SupportsX402ResourceServer", FakeX402ResourceServer()),
        settings=Settings(),
    )


@pytest.mark.asyncio
async def test_handle_paid_invoke_replays_consumed_attempt_without_facilitator_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(raise_integrity_on_commit=True)
    facilitator_client = FakeFacilitatorClient()
    existing_attempt = FakeAttempt(
        id=41,
        invocation_id=88,
        quote_id=21,
        idempotency_key="invoke-key",
        status=PaymentAttemptStatus.CONSUMED,
        settle_outcome={"ok": True, "reference": "settle-1"},
    )
    invoke_service = FakeInvokeService(invocation_for_lookup=FakeInvocation(id=88))
    service = _build_service(session, facilitator_client=facilitator_client)
    service._attempt_repo = FakePaymentAttemptRepository(existing_attempt=existing_attempt)
    service._invoke_service = invoke_service
    monkeypatch.setattr(
        service,
        "_build_requirement",
        lambda *, amount_minor, currency: {
            "amount_minor": amount_minor,
            "currency": currency,
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "payment_amount": 5_000_000,
        },
    )

    result = await service.handle_paid_invoke(
        ActorContext(account_id=12),
        resolved=_resolved_target(),
        idempotency_key="invoke-key",
        request_headers={"PAYMENT-SIGNATURE": _payment_header(payment_identifier="payment-1")},
    )

    assert isinstance(result, PaidInvokeSuccess)
    assert result.invocation.id == 88
    assert result.response_headers == {"PAYMENT-RESPONSE": "settle-1"}
    assert facilitator_client.verify_calls == 0
    assert facilitator_client.settle_calls == 0
    assert invoke_service.get_invocation_calls == 1
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_handle_paid_invoke_replays_terminal_settle_failure_without_facilitator_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(raise_integrity_on_commit=True)
    facilitator_client = FakeFacilitatorClient()
    existing_attempt = FakeAttempt(
        id=41,
        invocation_id=77,
        quote_id=21,
        idempotency_key="invoke-key-1",
        status=PaymentAttemptStatus.SETTLE_FAILED,
        settle_outcome={"ok": True, "reference": "settle-1"},
    )
    service = _build_service(session, facilitator_client=facilitator_client)
    service._attempt_repo = FakePaymentAttemptRepository(existing_attempt=existing_attempt)
    service._invoke_service = FakeInvokeService()
    monkeypatch.setattr(
        service,
        "_build_requirement",
        lambda *, amount_minor, currency: {
            "amount_minor": amount_minor,
            "currency": currency,
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "payment_amount": 5_000_000,
        },
    )

    with pytest.raises(InvokeBadGatewayError, match="payment settlement failed"):
        await service.handle_paid_invoke(
            ActorContext(account_id=12),
            resolved=_resolved_target(),
            idempotency_key="invoke-key",
            request_headers={"PAYMENT-SIGNATURE": _payment_header(payment_identifier="payment-1")},
        )

    assert facilitator_client.verify_calls == 0
    assert facilitator_client.settle_calls == 0
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_handle_paid_invoke_marks_compensation_required_after_settled_invoke_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    facilitator_client = FakeFacilitatorClient(
        verify_outcomes=[{"ok": True, "reference": "verify-1"}],
        settle_outcomes=[{"ok": True, "reference": "settle-1"}],
    )
    invoke_service = FakeInvokeService(
        invocation_for_lookup=FakeInvocation(id=77),
        execute_exception=InvokeBadGatewayError("upstream request failed"),
    )
    service = _build_service(session, facilitator_client=facilitator_client)
    service._attempt_repo = FakePaymentAttemptRepository()
    service._invoke_service = invoke_service
    service._ledger_service = FakeLedgerService()
    monkeypatch.setattr(
        service,
        "_build_requirement",
        lambda *, amount_minor, currency: {
            "amount_minor": amount_minor,
            "currency": currency,
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "payment_amount": 5_000_000,
        },
    )

    with pytest.raises(InvokeBadGatewayError, match="upstream request failed"):
        await service.handle_paid_invoke(
            ActorContext(account_id=12),
            resolved=_resolved_target(),
            idempotency_key="invoke-key",
            request_headers={
                "PAYMENT-SIGNATURE": _payment_header(payment_identifier="payment-compensate")
            },
        )

    attempt = service._attempt_repo.added_attempt
    assert attempt is not None
    assert attempt.status is PaymentAttemptStatus.SETTLED
    assert attempt.invocation_id is None
    assert attempt.verify_outcome == {"ok": True, "reference": "verify-1"}
    assert attempt.settle_outcome == {"ok": True, "reference": "settle-1"}
    assert session.commit_calls == 3
    assert service._ledger_service.record_calls == []
    assert invoke_service.execute_calls == 1
    assert invoke_service.get_invocation_by_idempotency_calls == 0


@pytest.mark.asyncio
async def test_verify_maps_facilitator_auth_failures_to_bad_gateway() -> None:
    class AuthFailingFacilitatorClient:
        async def verify(
            self,
            *,
            payment_requirement: dict[str, object],
            payment_payload: dict[str, object],
        ) -> dict[str, object]:
            _ = payment_requirement
            _ = payment_payload
            raise FacilitatorAuthError("facilitator authentication failed")

        async def settle(
            self,
            *,
            payment_requirement: dict[str, object],
            payment_payload: dict[str, object],
        ) -> dict[str, object]:
            _ = payment_requirement
            _ = payment_payload
            raise AssertionError("settle should not be called")

    service = PaymentService(
        cast("AsyncSession", object()),
        http_client=cast("SupportsRequest", FakeHttpClient()),
        facilitator_client=cast("SupportsFacilitatorClient", AuthFailingFacilitatorClient()),
        x402_resource_server=cast("SupportsX402ResourceServer", FakeX402ResourceServer()),
        settings=Settings(),
    )

    with pytest.raises(InvokeBadGatewayError, match="facilitator authentication failed"):
        await service._verify(
            payment_requirement={"amount_minor": 500},
            payment_payload={"authorization": {"nonce": "payment-1"}},
        )


@pytest.mark.asyncio
async def test_handle_paid_invoke_resumes_from_settled_attempt_after_final_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePayoutExecutionService:
        def __init__(self, session: object) -> None:
            _ = session

        async def record_ready_payout(self, **kwargs: object) -> None:
            _ = kwargs

    attempt_repo = FakePersistedPaymentAttemptRepository()
    session = FakeCommitSequenceSession(
        fail_on_commit_calls={4},
        on_successful_commit=attempt_repo.persist,
    )
    facilitator_client = FakeFacilitatorClient(
        verify_outcomes=[{"ok": True, "reference": "verify-1"}],
        settle_outcomes=[{"ok": True, "reference": "settle-1"}],
    )
    invoke_service = FakeIdempotentInvokeService()
    service = _build_service(session, facilitator_client=facilitator_client)
    service._attempt_repo = attempt_repo
    service._invoke_service = invoke_service
    service._ledger_service = FakeLedgerService()
    monkeypatch.setattr(
        payment_service_module,
        "PayoutExecutionService",
        FakePayoutExecutionService,
    )
    monkeypatch.setattr(
        service,
        "_build_requirement",
        lambda *, amount_minor, currency: {
            "amount_minor": amount_minor,
            "currency": currency,
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "payment_amount": 5_000_000,
        },
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.handle_paid_invoke(
            ActorContext(account_id=12),
            resolved=_resolved_target(),
            idempotency_key="invoke-key",
            request_headers={"PAYMENT-SIGNATURE": _payment_header(payment_identifier="payment-1")},
        )

    assert attempt_repo.stored_attempt is not None
    assert attempt_repo.stored_attempt.status is PaymentAttemptStatus.SETTLED
    assert facilitator_client.verify_calls == 1
    assert facilitator_client.settle_calls == 1

    result = await service.handle_paid_invoke(
        ActorContext(account_id=12),
        resolved=_resolved_target(),
        idempotency_key="invoke-key",
        request_headers={"PAYMENT-SIGNATURE": _payment_header(payment_identifier="payment-1")},
    )

    assert isinstance(result, PaidInvokeSuccess)
    assert attempt_repo.add_calls == 1
    assert attempt_repo.stored_attempt is not None
    assert attempt_repo.stored_attempt.status is PaymentAttemptStatus.CONSUMED
    assert attempt_repo.stored_attempt.invocation_id == 88
    assert session.commit_calls == 5
    assert facilitator_client.verify_calls == 1
    assert facilitator_client.settle_calls == 1
    assert invoke_service.execute_calls == 2
    assert invoke_service.side_effect_idempotency_keys == {"invoke-key"}
