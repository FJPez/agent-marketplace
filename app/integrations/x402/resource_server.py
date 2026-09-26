from __future__ import annotations

from typing import TYPE_CHECKING

from x402 import PaymentRequired
from x402.http import encode_payment_required_header, encode_payment_response_header

from app.integrations.x402.headers import PAYMENT_REQUIRED_HEADER, PAYMENT_RESPONSE_HEADER

if TYPE_CHECKING:
    from app.integrations.x402.models import PaymentRequirement, SettleOutcome


class X402ResourceServerAdapter:
    def build_payment_required_headers(
        self,
        *,
        requirement: PaymentRequirement,
    ) -> dict[str, str]:
        header_value = encode_payment_required_header(
            PaymentRequired(accepts=[requirement.to_sdk()])
        )
        return {PAYMENT_REQUIRED_HEADER: header_value}

    def build_payment_response_headers(
        self,
        *,
        outcome: SettleOutcome,
    ) -> dict[str, str]:
        return {PAYMENT_RESPONSE_HEADER: encode_payment_response_header(outcome.to_sdk())}
