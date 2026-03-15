from __future__ import annotations

from x402 import PaymentRequired
from x402.http import encode_payment_required_header, encode_payment_response_header

from app.integrations.x402.models import to_payment_requirements, to_settle_response


class X402ResourceServerAdapter:
    def build_payment_required_headers(
        self,
        *,
        payment_requirement: dict[str, object],
    ) -> dict[str, str]:
        header_value = encode_payment_required_header(
            PaymentRequired(accepts=[to_payment_requirements(payment_requirement)])
        )
        return {"X-PAYMENT-REQUIRED": header_value}

    def build_payment_response_headers(
        self,
        *,
        settle_outcome: dict[str, object],
    ) -> dict[str, str]:
        return {
            "X-PAYMENT-RESPONSE": encode_payment_response_header(to_settle_response(settle_outcome))
        }
