from datetime import UTC, datetime, timedelta

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import (
    TokenPayload,
    checksum_address,
    decode_token,
    encode_token,
    generate_api_key,
    hash_api_key,
    parse_siwe_message,
    verify_siwe_signature,
)

# An address in its EIP-55 form, and the same address with one letter's case flipped.
CHECKSUMMED_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
MISTYPED_ADDRESS = "0x036cbD53842c5426634e7929541eC2318f3dCF7e"


def _settings() -> Settings:
    return Settings(
        jwt_secret_key=SecretStr("test-secret-key-with-32-bytes-123"),
        siwe_domain="testserver",
    )


@pytest.mark.parametrize(
    "address",
    [CHECKSUMMED_ADDRESS.lower(), "0x" + CHECKSUMMED_ADDRESS[2:].upper(), CHECKSUMMED_ADDRESS],
    ids=["lowercase", "uppercase", "checksummed"],
)
def test_checksum_address_returns_the_eip55_form(address: str) -> None:
    assert checksum_address(address) == CHECKSUMMED_ADDRESS


@pytest.mark.parametrize(
    ("address", "message"),
    [
        (MISTYPED_ADDRESS, "address has an invalid EIP-55 checksum"),
        ("0x1234", "invalid EVM address"),
        (CHECKSUMMED_ADDRESS[2:], "invalid EVM address"),
        ("0X" + CHECKSUMMED_ADDRESS[2:], "invalid EVM address"),
        (" " + CHECKSUMMED_ADDRESS, "invalid EVM address"),
        (CHECKSUMMED_ADDRESS + " ", "invalid EVM address"),
        (CHECKSUMMED_ADDRESS[:-1], "invalid EVM address"),
        (CHECKSUMMED_ADDRESS + "e", "invalid EVM address"),
        (CHECKSUMMED_ADDRESS[:-1] + "g", "invalid EVM address"),
    ],
    ids=[
        "mistyped_checksum",
        "not_an_address",
        "missing_0x_prefix",
        "uppercase_0x_prefix",
        "leading_space",
        "trailing_space",
        "too_short",
        "too_long",
        "non_hex_character",
    ],
)
def test_checksum_address_rejects_what_is_not_a_well_typed_address(
    address: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        checksum_address(address)


def test_parse_siwe_message_rejects_an_address_with_a_mistyped_checksum() -> None:
    message = "\n".join(
        [
            "testserver wants you to sign in with your Ethereum account:",
            MISTYPED_ADDRESS,
            "",
            "URI: http://testserver",
            "Version: 1",
            "Chain ID: 1",
            "Nonce: abc123",
            "Issued At: 2026-03-16T12:00:00Z",
        ],
    )

    with pytest.raises(ValueError, match="address has an invalid EIP-55 checksum"):
        parse_siwe_message(message)


def test_encode_and_decode_token_round_trip() -> None:
    settings = _settings()
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    payload = TokenPayload(
        subject="12",
        wallet_address="0x1234567890123456789012345678901234567890",
        token_version=3,
        token_type="access",
        expires_at=expires_at,
    )

    token = encode_token(settings, payload)
    decoded = decode_token(settings, token, expected_type="access")

    assert decoded.subject == "12"
    assert decoded.token_version == 3
    assert decoded.token_type == "access"


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
        expected_nonce="abc123",
        now=issued_at + timedelta(seconds=1),
    )

    assert parsed.address == signer.address


def test_verify_siwe_signature_rejects_wrong_nonce() -> None:
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

    with pytest.raises(ValueError, match="nonce is not valid"):
        verify_siwe_signature(
            settings,
            message=message,
            signature=signed.signature.to_0x_hex(),
            expected_nonce="wrong",
            now=issued_at + timedelta(seconds=1),
        )


def test_generate_api_key_returns_prefixed_plaintext_and_hash() -> None:
    material = generate_api_key("amp_")

    assert material.plaintext.startswith("amp_")
    assert material.key_prefix == material.plaintext[:16]
    assert material.key_hash == hash_api_key(material.plaintext)
