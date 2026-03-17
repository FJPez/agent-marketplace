from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.exc import IntegrityError
from x402 import PaymentPayload
from x402.http import encode_payment_signature_header

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.enums import AccessMode, PricingModelType, ServiceLifecycle
from app.db.models import Quote, Service, ServiceEndpoint
from app.integrations.provider_gateway.signing import HmacAuthConfig
from app.integrations.x402.facilitator_client import FacilitatorAuthError
from app.services.invoke_service import (
    InvokeBadGatewayError,
    InvokeConflictError,
    ResolvedInvokeTarget,
)
from app.services.payment_service import PaymentService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.models import Invocation
    from app.integrations.provider_gateway.client import SupportsRequest
    from app.services.payment_service import SupportsFacilitatorClient, SupportsX402ResourceServer


class FakeSession:
    def __init__(self) -> None:
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.begin_nested_calls = 0

    async def flush(self) -> None:
        self.flush_calls += 1
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


class FakeAttempt:
    def __init__(self, **kwargs: object) -> None:
        self.invocation_id = cast("int | None", kwargs["invocation_id"])
        self.quote_id = cast("int", kwargs["quote_id"])
        self.settle_outcome = cast("dict[str, object] | None", kwargs["settle_outcome"])


class FakePaymentAttemptRepository:
    def __init__(self) -> None:
        self.add_calls = 0

    async def get_by_payment_identifier(self, *, payment_identifier: str) -> None:
        _ = payment_identifier

    def add(self, **kwargs: object) -> FakeAttempt:
        self.add_calls += 1
        return FakeAttempt(**kwargs)


class FakeInvokeService:
    async def get_replayable_invocation(
        self,
        actor: ActorContext,
        *,
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        _ = actor
        _ = idempotency_key
        _ = request_hash

    async def execute(
        self,
        actor: ActorContext,
        *,
        resolved: ResolvedInvokeTarget,
        idempotency_key: str,
        auto_commit: bool = True,
    ) -> "Invocation":
        _ = actor
        _ = resolved
        _ = idempotency_key
        _ = auto_commit
        msg = "execute should not be reached in duplicate-claim test"
        raise AssertionError(msg)


class FakeFacilitatorClient:
    def __init__(self) -> None:
        self.verify_calls = 0
        self.settle_calls = 0

    async def verify(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        _ = payment_requirement
        _ = payment_payload
        self.verify_calls += 1
        return {"ok": True, "reference": "verify-1"}

    async def settle(
        self,
        *,
        payment_requirement: dict[str, object],
        payment_payload: dict[str, object],
    ) -> dict[str, object]:
        _ = payment_requirement
        _ = payment_payload
        self.settle_calls += 1
        return {"ok": True, "reference": "settle-1"}


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
        _ = settle_outcome
        return {}


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
        msg = "http client should not be used in duplicate-claim test"
        raise AssertionError(msg)

    async def aclose(self) -> None:
        return None


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


@pytest.mark.asyncio
async def test_handle_paid_invoke_conflicts_before_verify_when_identifier_claim_is_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    facilitator_client = FakeFacilitatorClient()
    service = PaymentService(
        cast("AsyncSession", session),
        http_client=cast("SupportsRequest", FakeHttpClient()),
        facilitator_client=cast("SupportsFacilitatorClient", facilitator_client),
        x402_resource_server=cast("SupportsX402ResourceServer", FakeX402ResourceServer()),
        settings=Settings(),
    )
    service._attempt_repo = FakePaymentAttemptRepository()
    service._invoke_service = FakeInvokeService()
    monkeypatch.setattr(
        service,
        "_build_requirement",
        lambda *, amount_minor, currency: {"amount_minor": amount_minor, "currency": currency},
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
    assert session.flush_calls == 1
    assert session.begin_nested_calls == 1
    assert service._attempt_repo.add_calls == 1


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
