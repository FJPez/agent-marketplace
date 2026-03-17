from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from eth_account import Account
from eth_account.messages import encode_defunct

if TYPE_CHECKING:
    import httpx

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_SIWE_DOMAIN = "127.0.0.1"
CHAIN_ID = 84532


def resolve_api_base_url() -> str:
    return os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL)


def get_required_private_key(env_name: str, *, purpose: str) -> str:
    private_key = os.getenv(env_name, "").strip()
    if private_key:
        return private_key
    raise RuntimeError(f"{env_name} is required for {purpose}")


def wallet_address_from_private_key(private_key: str, *, env_name: str) -> str:
    try:
        return Account.from_key(private_key).address
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{env_name} is not a valid EVM private key") from exc


def ensure_distinct_wallets(
    *,
    primary_private_key: str,
    primary_env_name: str,
    secondary_private_key: str | None,
    secondary_env_name: str,
) -> tuple[str, str | None]:
    primary_address = wallet_address_from_private_key(
        primary_private_key,
        env_name=primary_env_name,
    )
    if secondary_private_key is None:
        return primary_address, None

    secondary_address = wallet_address_from_private_key(
        secondary_private_key,
        env_name=secondary_env_name,
    )
    if primary_address.lower() == secondary_address.lower():
        raise RuntimeError(
            f"{primary_env_name} and {secondary_env_name} must resolve to different wallets",
        )
    return primary_address, secondary_address


async def authenticate(
    client: httpx.AsyncClient,
    *,
    api_base_url: str,
    private_key: str,
) -> tuple[str, str]:
    wallet_address = wallet_address_from_private_key(private_key, env_name="private key")
    nonce_response = await client.get("/v1/auth/nonce", params={"address": wallet_address})
    nonce_response.raise_for_status()
    nonce = nonce_response.json()["nonce"]
    issued_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    message = "\n".join(
        [
            f"{resolve_siwe_domain(api_base_url)} wants you to sign in with your Ethereum account:",
            wallet_address,
            "",
            f"URI: {api_base_url}",
            "Version: 1",
            f"Chain ID: {CHAIN_ID}",
            f"Nonce: {nonce}",
            f"Issued At: {issued_at}",
        ],
    )
    signed = Account.sign_message(
        signable_message=encode_defunct(text=message),
        private_key=private_key,
    )
    verify_response = await client.post(
        "/v1/auth/verify",
        json={
            "message": message,
            "signature": signed.signature.to_0x_hex(),
        },
    )
    verify_response.raise_for_status()
    return verify_response.json()["access_token"], wallet_address


def authorization_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def build_idempotency_headers(access_token: str, *, prefix: str) -> dict[str, str]:
    return {
        **authorization_headers(access_token),
        "Idempotency-Key": f"{prefix}-{uuid.uuid4()}",
    }


def resolve_siwe_domain(api_base_url: str) -> str:
    configured_domain = os.getenv("SIWE_DOMAIN", "").strip()
    if configured_domain:
        return configured_domain
    parsed = urlparse(api_base_url)
    if parsed.hostname:
        return parsed.hostname
    return DEFAULT_SIWE_DOMAIN
