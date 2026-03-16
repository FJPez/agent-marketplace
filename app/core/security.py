from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from secrets import token_urlsafe
from typing import TYPE_CHECKING

import jwt
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import is_address, to_checksum_address
from jwt import InvalidTokenError

if TYPE_CHECKING:
    from app.core.config import Settings


class AuthTokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    account_id: int
    wallet_address: str
    token_version: int
    token_type: AuthTokenType


@dataclass(frozen=True, slots=True)
class TokenPayload:
    subject: str
    wallet_address: str
    token_version: int
    token_type: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ParsedSiweMessage:
    domain: str
    address: str
    uri: str
    version: str
    chain_id: int
    nonce: str
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class ApiKeyMaterial:
    plaintext: str
    key_prefix: str
    key_hash: str


def normalize_wallet_address(wallet_address: str) -> str:
    if not is_address(wallet_address):
        msg = "invalid wallet address"
        raise ValueError(msg)
    return to_checksum_address(wallet_address)


def create_jwt(
    *,
    secret_key: str,
    account_id: int,
    wallet_address: str,
    token_version: int,
    token_type: AuthTokenType,
    expires_in_seconds: int,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "sub": str(account_id),
        "wallet": wallet_address,
        "tv": token_version,
        "type": token_type.value,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


def encode_token(settings: Settings, payload: TokenPayload) -> str:
    return jwt.encode(
        {
            "sub": payload.subject,
            "wallet": normalize_wallet_address(payload.wallet_address),
            "tv": payload.token_version,
            "type": payload.token_type,
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int(payload.expires_at.timestamp()),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )


def decode_jwt(
    token: str,
    *,
    secret_key: str,
    expected_token_type: AuthTokenType,
    now: datetime | None = None,
) -> TokenClaims:
    current_time = now or datetime.now(UTC)
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=["HS256"],
            options={"verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("invalid token") from exc

    expires_at = payload.get("exp")
    if not isinstance(expires_at, int) or expires_at < int(current_time.timestamp()):
        raise InvalidTokenError("token expired")

    token_type = payload.get("type")
    if token_type != expected_token_type.value:
        raise InvalidTokenError("unexpected token type")

    sub = payload.get("sub")
    wallet_address = payload.get("wallet")
    token_version = payload.get("tv")
    if not isinstance(sub, str) or not sub.isdigit():
        raise InvalidTokenError("invalid token subject")
    if not isinstance(wallet_address, str):
        raise InvalidTokenError("invalid wallet claim")
    if not isinstance(token_version, int):
        raise InvalidTokenError("invalid token version")

    return TokenClaims(
        account_id=int(sub),
        wallet_address=wallet_address,
        token_version=token_version,
        token_type=expected_token_type,
    )


def decode_token(
    settings: Settings,
    token: str,
    *,
    expected_type: str,
    now: datetime | None = None,
) -> TokenPayload:
    claims = decode_jwt(
        token,
        secret_key=settings.jwt_secret_key,
        expected_token_type=AuthTokenType(expected_type),
        now=now,
    )
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=["HS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    expires_at = datetime.fromtimestamp(int(payload["exp"]), UTC)
    return TokenPayload(
        subject=str(claims.account_id),
        wallet_address=claims.wallet_address,
        token_version=claims.token_version,
        token_type=claims.token_type.value,
        expires_at=expires_at,
    )


def parse_siwe_message(message: str) -> ParsedSiweMessage:
    lines = message.splitlines()
    if len(lines) < 8:
        msg = "invalid SIWE message"
        raise ValueError(msg)

    first_line = lines[0]
    suffix = " wants you to sign in with your Ethereum account:"
    if not first_line.endswith(suffix):
        msg = "invalid SIWE message"
        raise ValueError(msg)

    domain = first_line[: -len(suffix)]
    address = normalize_wallet_address(lines[1].strip())
    fields: dict[str, str] = {}
    for line in lines[3:]:
        if not line:
            continue
        key, separator, value = line.partition(": ")
        if not separator:
            continue
        fields[key] = value

    try:
        uri = fields["URI"]
        version = fields["Version"]
        chain_id = int(fields["Chain ID"])
        nonce = fields["Nonce"]
        issued_at = datetime.fromisoformat(fields["Issued At"].replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        msg = "invalid SIWE message"
        raise ValueError(msg) from exc

    return ParsedSiweMessage(
        domain=domain,
        address=address,
        uri=uri,
        version=version,
        chain_id=chain_id,
        nonce=nonce,
        issued_at=issued_at.astimezone(UTC),
    )


def verify_siwe_signature(
    settings: Settings,
    *,
    message: str,
    signature: str,
    expected_nonce: str,
    now: datetime | None = None,
) -> ParsedSiweMessage:
    parsed = parse_siwe_message(message)
    if parsed.domain != settings.siwe_domain:
        msg = "SIWE domain is not valid"
        raise ValueError(msg)
    if parsed.nonce != expected_nonce:
        msg = "nonce is not valid"
        raise ValueError(msg)

    current_time = now or datetime.now(UTC)
    if current_time - parsed.issued_at > timedelta(seconds=settings.siwe_nonce_expiry):
        msg = "SIWE message has expired"
        raise ValueError(msg)

    try:
        recovered_address = Account.recover_message(
            encode_defunct(text=message),
            signature=signature,
        )
    except Exception as exc:
        msg = "signature is not valid"
        raise ValueError(msg) from exc

    if normalize_wallet_address(recovered_address) != parsed.address:
        msg = "signature is not valid"
        raise ValueError(msg)

    return parsed


def generate_nonce() -> str:
    return token_urlsafe(24)


def generate_api_key(prefix: str) -> ApiKeyMaterial:
    secret = token_urlsafe(32)
    plaintext = f"{prefix}{secret}"
    return ApiKeyMaterial(
        plaintext=plaintext,
        key_prefix=plaintext[:16],
        key_hash=hash_api_key(plaintext),
    )


def hash_api_key(api_key: str) -> str:
    return sha256(api_key.encode()).hexdigest()
