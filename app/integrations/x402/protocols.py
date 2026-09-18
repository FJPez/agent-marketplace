"""The x402 collaborators the payment flow depends on, as structural types."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.integrations.x402.models import (
    PaymentPayload,
    PaymentRequirement,
    SettleOutcome,
    VerifyOutcome,
)


@runtime_checkable
class SupportsFacilitatorClient(Protocol):
    async def verify(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> VerifyOutcome: ...

    async def settle(
        self,
        *,
        requirement: PaymentRequirement,
        payload: PaymentPayload,
    ) -> SettleOutcome: ...


@runtime_checkable
class SupportsX402ResourceServer(Protocol):
    def build_payment_required_headers(
        self,
        *,
        requirement: PaymentRequirement,
    ) -> dict[str, str]: ...

    def build_payment_response_headers(
        self,
        *,
        outcome: SettleOutcome,
    ) -> dict[str, str]: ...
