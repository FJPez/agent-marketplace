from datetime import UTC, datetime, timedelta

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys.constants import SECPK1_N

from app.core.config import Settings
from app.core.security import (
    AuthTokenType,
    TokenClaims,
    canonical_signature,
    create_jwt,
    decode_jwt,
    generate_api_key,
    hash_api_key,
    parse_siwe_message,
    verify_siwe_signature,
)

# An EIP-55 address with one letter's case flipped.
MISTYPED_ADDRESS = "0x036cbD53842c5426634e7929541eC2318f3dCF7e"
# The r and s of a signature, in hex: s at most half the curve order (low s) or above it.
SIGNATURE_R = "ab" * 32
LOW_S = format(SECPK1_N // 2, "064x")
HIGH_S = format(SECPK1_N // 2 + 1, "064x")


def _settings() -> Settings:
    return Settings(
        jwt_secret_key="test-secret-key-with-32-bytes-123",
        siwe_domain="testserver",
    )


@pytest.mark.parametrize(
    ("signature", "canonical"),
    [
        pytest.param(f"0x{SIGNATURE_R}{LOW_S}1b", f"0x{SIGNATURE_R}{LOW_S}1b", id="v_27"),
        pytest.param(f"0x{SIGNATURE_R}{LOW_S}1c", f"0x{SIGNATURE_R}{LOW_S}1c", id="v_28"),
        pytest.param(f"0x{SIGNATURE_R}{LOW_S}00", f"0x{SIGNATURE_R}{LOW_S}1b", id="v_0"),
        pytest.param(f"0x{SIGNATURE_R}{LOW_S}01", f"0x{SIGNATURE_R}{LOW_S}1c", id="v_1"),
        pytest.param(
            f"0x{SIGNATURE_R}{LOW_S}1B".upper().replace("0X", "0x"),
            f"0x{SIGNATURE_R}{LOW_S}1b",
            id="uppercase_hex",
        ),
    ],
)
def test_canonical_signature_returns_low_s_and_v_27_or_28_in_lowercase_hex(
    signature: str,
    canonical: str,
) -> None:
    assert canonical_signature(signature) == canonical


@pytest.mark.parametrize(
    "signature",
    [
        pytest.param(f"0x{SIGNATURE_R}{HIGH_S}1b", id="high_s"),
        pytest.param(f"0x{SIGNATURE_R}{LOW_S}25", id="eip155_v_37"),
        pytest.param(f"0x{SIGNATURE_R}{LOW_S}02", id="v_2"),
        pytest.param(f"0x{SIGNATURE_R}{LOW_S}", id="no_v"),
        pytest.param(f"{SIGNATURE_R}{LOW_S}1b", id="missing_0x_prefix"),
        pytest.param(f"0x{SIGNATURE_R}{LOW_S}1b\n", id="trailing_newline"),
    ],
)
def test_canonical_signature_refuses_a_malformed_or_non_canonical_signature(
    signature: str,
) -> None:
    with pytest.raises(ValueError, match="signature is not valid"):
        canonical_signature(signature)


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


def test_create_and_decode_jwt_round_trip() -> None:
    secret_key = _settings().jwt_secret_key
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
