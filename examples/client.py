from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

import httpx
from eth_account import Account
from x402 import x402Client
from x402.http import x402HTTPClient
from x402.mechanisms.evm.exact import register_exact_evm_client

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
CONSUMER_ACCOUNT_ID = os.getenv("CONSUMER_ACCOUNT_ID", "2")
CONSUMER_PRIVATE_KEY = os.getenv("CONSUMER_PRIVATE_KEY")
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
    logger.info("Using consumer account id: %s", CONSUMER_ACCOUNT_ID)
    logger.info("Using wallet address: %s", wallet_address)
    logger.info("Target network: Base Sepolia (%s, chain %s)", NETWORK_CAIP2, CHAIN_ID)

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        services = await _discover_services(client)
        _select_service(services, SERVICE_SLUG)

        free_payload = {"message": "hello from the free invoke example"}
        paid_payload = {
            "message": ("Summarize this marketplace request after the x402 payment flow completes.")
        }

        quote = await _create_quote(client, SERVICE_SLUG, paid_payload)
        await _invoke_free(client, SERVICE_SLUG, free_payload)
        await _invoke_paid(
            client,
            buyer,
            SERVICE_SLUG,
            paid_payload,
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
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/services/{service_slug}/quote",
        headers={"X-Account-Id": CONSUMER_ACCOUNT_ID},
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
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/invoke/{service_slug}",
        headers=_invoke_headers(),
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
    quote_id: int,
) -> dict[str, Any]:
    request_headers = _invoke_headers()
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

    response.raise_for_status()
    body = response.json()
    logger.info("\nPaid invoke:\n%s", json.dumps(body, indent=2))
    if payment_response := response.headers.get("PAYMENT-RESPONSE"):
        logger.info("PAYMENT-RESPONSE: %s", payment_response)
    return body


def _build_x402_http_client(private_key: str) -> tuple[x402HTTPClient, str]:
    wallet = Account.from_key(private_key)
    buyer = x402Client()
    register_exact_evm_client(buyer, wallet, networks=NETWORK_CAIP2)
    return x402HTTPClient(buyer), wallet.address


def _invoke_headers() -> dict[str, str]:
    return {
        "X-Account-Id": CONSUMER_ACCOUNT_ID,
        "Idempotency-Key": f"example-{uuid.uuid4()}",
    }


if __name__ == "__main__":
    asyncio.run(main())
