from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from x402 import x402Client
from x402.http import decode_payment_response_header, x402HTTPClient
from x402.mechanisms.evm.exact import register_exact_evm_client

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
CONSUMER_PRIVATE_KEY = os.getenv("CONSUMER_PRIVATE_KEY")
SIWE_DOMAIN = os.getenv("SIWE_DOMAIN")
SERVICE_SLUG = os.getenv("SERVICE_SLUG", "demo-agent-service")
FREE_ENDPOINT_KEY = "free-ping"
PAID_ENDPOINT_KEY = "paid-summary"
NETWORK_CAIP2 = "eip155:84532"
CHAIN_ID = 84532

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("example_client")


async def main() -> None:
    if not CONSUMER_PRIVATE_KEY:
        raise RuntimeError("CONSUMER_PRIVATE_KEY is required for the paid invoke example")

    buyer, wallet_address = _build_x402_http_client(CONSUMER_PRIVATE_KEY)

    logger.info("API base URL: %s", API_BASE_URL)
    logger.info("Using wallet address: %s", wallet_address)
    logger.info("Target network: Base Sepolia (%s, chain %s)", NETWORK_CAIP2, CHAIN_ID)

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        access_token = await _authenticate(client, private_key=CONSUMER_PRIVATE_KEY)
        services = await _discover_services(client)
        _select_service(services, SERVICE_SLUG)

        free_payload = {"message": "hello from the free invoke example"}
        paid_payload = {
            "message": ("Summarize this marketplace request after the x402 payment flow completes.")
        }

        quote = await _create_quote(client, SERVICE_SLUG, paid_payload, access_token=access_token)
        await _invoke_free(client, SERVICE_SLUG, free_payload, access_token=access_token)
        await _invoke_paid(
            client,
            buyer,
            SERVICE_SLUG,
            paid_payload,
            access_token=access_token,
            quote_id=quote["id"],
        )


async def _discover_services(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get("/v1/services")
    response.raise_for_status()
    services = response.json()
    logger.info("\nServices:\n%s", json.dumps(services, indent=2))
    return services


def _select_service(services: list[dict[str, Any]], service_slug: str) -> dict[str, Any]:
    for service in services:
        if service.get("slug") == service_slug:
            logger.info("\nSelected service: %s", service_slug)
            return service

    raise RuntimeError(f"Service {service_slug!r} was not returned by GET /v1/services")


async def _create_quote(
    client: httpx.AsyncClient,
    service_slug: str,
    payload: dict[str, object],
    *,
    access_token: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/services/{service_slug}/quote",
        headers=_authorization_headers(access_token),
        json={"endpoint_key": PAID_ENDPOINT_KEY, "payload": payload},
    )
    response.raise_for_status()
    quote = response.json()
    logger.info("\nQuote:\n%s", json.dumps(quote, indent=2))
    return quote


async def _invoke_free(
    client: httpx.AsyncClient,
    service_slug: str,
    payload: dict[str, object],
    *,
    access_token: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/invoke/{service_slug}",
        headers=_invoke_headers(access_token),
        json={"endpoint_key": FREE_ENDPOINT_KEY, "payload": payload},
    )
    response.raise_for_status()
    body = response.json()
    logger.info("\nFree invoke:\n%s", json.dumps(body, indent=2))
    return body


async def _invoke_paid(
    client: httpx.AsyncClient,
    buyer: x402HTTPClient,
    service_slug: str,
    payload: dict[str, object],
    *,
    access_token: str,
    quote_id: int,
) -> dict[str, Any]:
    request_headers = _invoke_headers(access_token)
    request_body = {
        "endpoint_key": PAID_ENDPOINT_KEY,
        "payload": payload,
        "quote_id": quote_id,
    }

    initial_response = await client.post(
        f"/v1/invoke/{service_slug}",
        headers=request_headers,
        json=request_body,
    )
    logger.info("\nPaid invoke initial status: %s", initial_response.status_code)
    if payment_required := initial_response.headers.get("PAYMENT-REQUIRED"):
        logger.info("PAYMENT-REQUIRED: %s", payment_required)

    response = initial_response
    if initial_response.status_code == 402:
        payment_headers, _ = await buyer.handle_402_response(
            dict(initial_response.headers),
            initial_response.content,
        )
        response = await client.post(
            f"/v1/invoke/{service_slug}",
            headers={**request_headers, **payment_headers},
            json=request_body,
        )

    if response.is_error:
        logger.info("Paid invoke failed status: %s", response.status_code)
        logger.info("Paid invoke failed body: %s", response.text)
    response.raise_for_status()
    body = response.json()
    logger.info("\nPaid invoke:\n%s", json.dumps(body, indent=2))
    if payment_response := response.headers.get("PAYMENT-RESPONSE"):
        logger.info("PAYMENT-RESPONSE: %s", payment_response)
        decoded_payment_response = decode_payment_response_header(payment_response)
        logger.info(
            "Decoded PAYMENT-RESPONSE:\n%s",
            json.dumps(
                decoded_payment_response.model_dump(by_alias=True, exclude_none=True),
                indent=2,
            ),
        )
        logger.info("Settlement transaction: %s", decoded_payment_response.transaction)
        logger.info(
            "Explorer URL: https://sepolia-explorer.base.org/tx/%s",
            decoded_payment_response.transaction,
        )
    return body


def _build_x402_http_client(private_key: str) -> tuple[x402HTTPClient, str]:
    wallet = Account.from_key(private_key)
    buyer = x402Client()
    register_exact_evm_client(buyer, wallet, networks=NETWORK_CAIP2)
    return x402HTTPClient(buyer), wallet.address


async def _authenticate(client: httpx.AsyncClient, *, private_key: str) -> str:
    signer = Account.from_key(private_key)
    nonce_response = await client.get(
        "/v1/auth/nonce",
        params={"address": signer.address},
    )
    nonce_response.raise_for_status()
    nonce = nonce_response.json()["nonce"]
    issued_at = datetime.now(UTC).replace(microsecond=0)
    domain = _resolve_siwe_domain()
    message = "\n".join(
        [
            f"{domain} wants you to sign in with your Ethereum account:",
            signer.address,
            "",
            f"URI: {API_BASE_URL}",
            "Version: 1",
            f"Chain ID: {CHAIN_ID}",
            f"Nonce: {nonce}",
            f"Issued At: {issued_at.isoformat().replace('+00:00', 'Z')}",
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
    body = verify_response.json()
    logger.info(
        "\nAuthenticated account:\n%s",
        json.dumps(body["account"], indent=2),
    )
    return body["access_token"]


def _resolve_siwe_domain() -> str:
    if SIWE_DOMAIN:
        return SIWE_DOMAIN
    parsed = urlparse(API_BASE_URL)
    if parsed.hostname:
        return parsed.hostname
    return "127.0.0.1"


def _authorization_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _invoke_headers(access_token: str) -> dict[str, str]:
    return {
        **_authorization_headers(access_token),
        "Idempotency-Key": f"example-{uuid.uuid4()}",
    }


if __name__ == "__main__":
    asyncio.run(main())
