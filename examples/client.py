from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
from demo_common import (
    CHAIN_ID,
    authenticate,
    authorization_headers,
    build_idempotency_headers,
    ensure_distinct_wallets,
    get_required_private_key,
    resolve_api_base_url,
)
from eth_account import Account
from x402 import x402Client
from x402.http import decode_payment_response_header, x402HTTPClient
from x402.mechanisms.evm.exact import register_exact_evm_client

API_BASE_URL = resolve_api_base_url()
SERVICE_SLUG = os.getenv("SERVICE_SLUG", "demo-agent-service")
FREE_ENDPOINT_KEY = "free-ping"
PAID_ENDPOINT_KEY = "paid-summary"
NETWORK_CAIP2 = "eip155:84532"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("example_client")


async def main() -> None:
    consumer_private_key = get_required_private_key(
        "CONSUMER_PRIVATE_KEY",
        purpose="the consumer paid invoke example",
    )
    consumer_wallet_address, provider_wallet_address = ensure_distinct_wallets(
        primary_private_key=consumer_private_key,
        primary_env_name="CONSUMER_PRIVATE_KEY",
        secondary_private_key=os.getenv("PROVIDER_PRIVATE_KEY"),
        secondary_env_name="PROVIDER_PRIVATE_KEY",
    )
    buyer, wallet_address = _build_x402_http_client(consumer_private_key)
    assert wallet_address == consumer_wallet_address

    logger.info("API base URL: %s", API_BASE_URL)
    logger.info("Consumer wallet address: %s", wallet_address)
    if provider_wallet_address is not None:
        logger.info("Provider wallet address: %s", provider_wallet_address)
    logger.info("Target network: Base Sepolia (%s, chain %s)", NETWORK_CAIP2, CHAIN_ID)

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        access_token, authenticated_wallet = await authenticate(
            client,
            api_base_url=API_BASE_URL,
            private_key=consumer_private_key,
        )
        logger.info("\nAuthenticated consumer wallet: %s", authenticated_wallet)
        services = await _discover_services(client)
        _select_service(services, SERVICE_SLUG)

        free_payload: dict[str, object] = {"message": "hello from the free invoke example"}
        paid_payload: dict[str, object] = {
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
        logger.info("\nProvider earnings should now exist as READY payouts.")
        logger.info(
            "Next step: run `make demo-provider` with PROVIDER_PRIVATE_KEY exported.",
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
        headers=authorization_headers(access_token),
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


def _invoke_headers(access_token: str) -> dict[str, str]:
    return build_idempotency_headers(access_token, prefix="example-consumer")


if __name__ == "__main__":
    asyncio.run(main())
