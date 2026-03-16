from x402.http import decode_payment_required_header, decode_payment_response_header

from app.integrations.x402.resource_server import X402ResourceServerAdapter


def test_build_payment_required_headers_uses_official_x402_encoding() -> None:
    headers = X402ResourceServerAdapter().build_payment_required_headers(
        payment_requirement={
            "scheme": "exact",
            "asset": "usdc",
            "amount_minor": 500,
            "currency": "USD",
            "pay_to": "0x000000000000000000000000000000000000c0de",
            "network": "base-sepolia",
            "network_caip2": "eip155:84532",
            "facilitator_url": "https://x402.org/facilitator",
            "max_timeout_seconds": 300,
        }
    )

    decoded = decode_payment_required_header(headers["PAYMENT-REQUIRED"])

    assert decoded.x402_version == 2
    assert len(decoded.accepts) == 1
    assert decoded.accepts[0].network == "eip155:84532"
    assert decoded.accepts[0].pay_to == "0x000000000000000000000000000000000000c0de"


def test_build_payment_response_headers_uses_official_x402_encoding() -> None:
    headers = X402ResourceServerAdapter().build_payment_response_headers(
        settle_outcome={
            "success": True,
            "transaction": "0xsettled",
            "network": "eip155:84532",
            "payer": "0xpayer",
        }
    )

    decoded = decode_payment_response_header(headers["PAYMENT-RESPONSE"])

    assert decoded.success is True
    assert decoded.transaction == "0xsettled"
    assert decoded.network == "eip155:84532"
    assert decoded.payer == "0xpayer"
