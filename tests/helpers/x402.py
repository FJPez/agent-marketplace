"""Builders for the x402 values that paid-invoke tests hand to the payment flow."""

from x402 import PaymentPayload as SdkPaymentPayload
from x402.http import encode_payment_signature_header

from app.integrations.x402.models import PaymentPayload, PaymentRequirement, SettleOutcome

PAYMENT_ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
TREASURY_ADDRESS = "0x000000000000000000000000000000000000c0de"


def payment_signature_header(
    *,
    payment_identifier: str = "payment-1",
    asset: str = PAYMENT_ASSET,
) -> str:
    return encode_payment_signature_header(
        SdkPaymentPayload.model_validate(
            {
                "payload": {
                    "authorization": {"nonce": payment_identifier},
                    "transaction": "0xabc123",
                },
                "accepted": {
                    "scheme": "exact",
                    "network": "eip155:84532",
                    "asset": asset,
                    "amount": "5000000",
                    "payTo": TREASURY_ADDRESS,
                    "maxTimeoutSeconds": 300,
                    "extra": {},
                },
            }
        )
    )


def build_payment_requirement(*, asset: str = PAYMENT_ASSET) -> PaymentRequirement:
    return PaymentRequirement(
        scheme="exact",
        asset=asset,
        amount_minor=500,
        payment_amount=5_000_000,
        currency="USD",
        pay_to=TREASURY_ADDRESS,
        network="base-sepolia",
        network_caip2="eip155:84532",
        facilitator_url="https://x402.org/facilitator",
        max_timeout_seconds=300,
        name="USDC",
        version="2",
    )


def build_payment_payload(*, payment_identifier: str = "payment-1") -> PaymentPayload:
    return PaymentPayload.from_header(
        payment_signature_header(payment_identifier=payment_identifier)
    )


def build_settle_outcome(*, transaction: str = "0xsettled") -> SettleOutcome:
    return SettleOutcome(
        success=True,
        transaction=transaction,
        network="eip155:84532",
        payer="0xpayer",
        amount="5000000",
    )
