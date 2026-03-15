import pytest
from x402 import PaymentPayload
from x402.http import encode_payment_signature_header

from app.integrations.x402.payment_identifier import (
    InvalidPaymentPayloadError,
    extract_payment_identifier,
    parse_payment_header,
)


def test_parse_payment_header_decodes_x402_v2_header_value() -> None:
    header_value = encode_payment_signature_header(
        PaymentPayload.model_validate(
            {
                "payload": {
                    "authorization": {"nonce": "payment-1"},
                    "transaction": "0xabc",
                },
                "accepted": {
                    "scheme": "exact",
                    "network": "eip155:84532",
                    "asset": "usdc",
                    "amount": "500",
                    "payTo": "0xabc",
                    "maxTimeoutSeconds": 300,
                    "extra": {},
                },
            }
        )
    )
    payload = parse_payment_header(header_value)

    assert payload["x402Version"] == 2
    assert payload["payload"] == {
        "authorization": {"nonce": "payment-1"},
        "transaction": "0xabc",
    }


def test_extract_payment_identifier_returns_identifier() -> None:
    identifier = extract_payment_identifier(
        {"payload": {"authorization": {"nonce": "payment-1"}, "transaction": "0xabc"}}
    )

    assert identifier == "payment-1"


def test_extract_payment_identifier_rejects_missing_identifier() -> None:
    with pytest.raises(InvalidPaymentPayloadError, match="payment identifier is missing"):
        extract_payment_identifier({})
