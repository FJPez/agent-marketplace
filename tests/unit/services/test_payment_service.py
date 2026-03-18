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
from app.services.invoke_service import (
    InvokeBadGatewayError,
    InvokeConflictError,
    ResolvedInvokeTarget,
)
from app.services.payment_service import PaidInvokeSuccess, PaymentService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.integrations.provider_gateway.client import SupportsRequest
    from app.services.payment_service import SupportsFacilitatorClient, SupportsX402ResourceServer


class FakeSession:
    def __init__(self, *, raise_integrity_on_flush: bool = False) -> None:
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.begin_nested_calls = 0
        self.raise_integrity_on_flush = raise_integrity_on_flush

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.raise_integrity_on_flush:
            raise IntegrityError("statement", {}, Exception("duplicate payment identifier"))

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1

    def begin_nested(self) -> "_FakeNestedTransaction":
        self.begin_nested_calls += 1
        return _FakeNestedTransaction()


class _FakeNestedTransaction:
    async def __aenter__(self) -> "_FakeNestedTransaction":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool:
        _ = exc_type
        _ = exc
        _ = tb
        return False


@dataclass
class FakeInvocation:
    id: int


@dataclass
class FakeAttempt:
    id: int
    invocation_id: int | None
    quote_id: int
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
            status=status,
            settle_outcome=settle_outcome,
            verify_outcome=verify_outcome,
            facilitator_reference=facilitator_reference,
        )
        self.added_attempt = attempt
        return attempt


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
        auto_commit: bool = True,
    ) -> FakeInvocation:
        _ = actor
        _ = resolved
        _ = idempotency_key
        _ = auto_commit
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


class FakeFacilitatorClient:
    def __init__(
        self,
        *,
        verify_outcomes: list[dict[str, object]] | None = None,
        settle_outcomes: list[dict[str, object]] | None = None,
    ) -> None:
        self.verify_calls = 0
        self.settle_calls = 0
        default_verify_outcomes: list[dict[str, object]] = [
            {"ok": True, "reference": "verify-1"}
        ]
        default_settle_outcomes: list[dict[str, object]] = [
            {"ok": True, "reference": "settle-1"}
        ]
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
        json: dict[str, object],
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
    session: FakeSession,
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
    session = FakeSession(raise_integrity_on_flush=True)
    facilitator_client = FakeFacilitatorClient()
    existing_attempt = FakeAttempt(
        id=41,
        invocation_id=88,
        quote_id=21,
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
async def test_handle_paid_invoke_conflicts_when_duplicate_attempt_is_not_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(raise_integrity_on_flush=True)
    facilitator_client = FakeFacilitatorClient()
    existing_attempt = FakeAttempt(
        id=41,
        invocation_id=77,
        quote_id=21,
        status=PaymentAttemptStatus.COMPENSATION_REQUIRED,
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

    with pytest.raises(InvokeConflictError, match="payment identifier already used"):
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
    assert attempt.status is PaymentAttemptStatus.COMPENSATION_REQUIRED
    assert attempt.invocation_id == 77
    assert attempt.verify_outcome == {"ok": True, "reference": "verify-1"}
    assert attempt.settle_outcome == {"ok": True, "reference": "settle-1"}
    assert session.commit_calls == 1
    assert service._ledger_service.record_calls == []
    assert invoke_service.execute_calls == 1
    assert invoke_service.get_invocation_by_idempotency_calls == 1


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
