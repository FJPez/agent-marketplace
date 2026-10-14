from datetime import UTC, datetime, timedelta

from eth_account import Account
from eth_account.messages import encode_defunct
from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import (
    AuthTokenType,
    TokenClaims,
    create_jwt,
    decode_jwt,
    generate_api_key,
    hash_api_key,
    parse_siwe_message,
    verify_siwe_signature,
)


def _settings() -> Settings:
    return Settings(
        jwt_secret_key=SecretStr("test-secret-key-with-32-bytes-123"),
        siwe_domain="testserver",
    )


def test_create_and_decode_jwt_round_trip() -> None:
    secret_key = _settings().jwt_secret_key.get_secret_value()
    wallet_address = "0x1234567890123456789012345678901234567890"

    token = create_jwt(
        secret_key=secret_key,
        account_id=12,
        wallet_address=wallet_address,
        token_version=3,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=900,
    )
    claims = decode_jwt(token, secret_key=secret_key, expected_token_type=AuthTokenType.ACCESS)

    assert claims == TokenClaims(
        account_id=12,
        wallet_address=wallet_address,
        token_version=3,
        token_type=AuthTokenType.ACCESS,
    )


def test_parse_siwe_message_extracts_expected_fields() -> None:
    issued_at = "2026-03-16T12:00:00Z"
    message = "\n".join(
        [
            "testserver wants you to sign in with your Ethereum account:",
            "0x1234567890123456789012345678901234567890",
            "",
            "URI: http://testserver",
            "Version: 1",
            "Chain ID: 1",
            "Nonce: abc123",
            f"Issued At: {issued_at}",
        ],
    )

    parsed = parse_siwe_message(message)

    assert parsed.domain == "testserver"
    assert parsed.address == "0x1234567890123456789012345678901234567890"
    assert parsed.uri == "http://testserver"
    assert parsed.version == "1"
    assert parsed.chain_id == 1
    assert parsed.nonce == "abc123"
    assert parsed.issued_at == datetime(2026, 3, 16, 12, 0, tzinfo=UTC)


def test_verify_siwe_signature_accepts_valid_message() -> None:
    settings = _settings()
    signer = Account.create()
    issued_at = datetime.now(UTC).replace(microsecond=0)
    message = "\n".join(
        [
            "testserver wants you to sign in with your Ethereum account:",
            signer.address,
            "",
            "URI: http://testserver",
            "Version: 1",
            "Chain ID: 1",
            "Nonce: abc123",
            f"Issued At: {issued_at.isoformat().replace('+00:00', 'Z')}",
        ],
    )
    signed = Account.sign_message(
        signable_message=encode_defunct(text=message),
        private_key=signer.key,
    )

    parsed = verify_siwe_signature(
        settings,
        message=message,
        signature=signed.signature.to_0x_hex(),
        now=issued_at + timedelta(seconds=1),
    )

    assert parsed.address == signer.address
    assert parsed.nonce == "abc123"


def test_generate_api_key_returns_prefixed_plaintext_and_hash() -> None:
    material = generate_api_key("amp_")

    assert material.plaintext.startswith("amp_")
    assert material.key_prefix == material.plaintext[:16]
    assert material.key_hash == hash_api_key(material.plaintext)
