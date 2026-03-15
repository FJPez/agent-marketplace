from app.integrations.provider_gateway.signing import build_signed_headers


def test_build_signed_headers_uses_expected_canonical_string() -> None:
    headers = build_signed_headers(
        key_id="gateway-key",
        secret="super-secret",
        http_method="POST",
        path="/invoke",
        request_hash="a" * 64,
        invocation_id=42,
        timestamp="1710500000",
    )

    assert headers == {
        "X-Agent-Marketplace-Key-Id": "gateway-key",
        "X-Agent-Marketplace-Timestamp": "1710500000",
        "X-Agent-Marketplace-Request-Hash": "a" * 64,
        "X-Agent-Marketplace-Invocation-Id": "42",
        "X-Agent-Marketplace-Signature": (
            "61aa4e85c873c3979712a9e1e76585ba787785ce876d25765b4c9654180d7812"
        ),
    }
