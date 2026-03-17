from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
from demo_common import (
    authenticate,
    build_idempotency_headers,
    ensure_distinct_wallets,
    get_required_private_key,
    resolve_api_base_url,
)

API_BASE_URL = resolve_api_base_url()
SERVICE_SLUG = os.getenv("SERVICE_SLUG", "demo-agent-service")
FREE_ENDPOINT_KEY = os.getenv("FREE_ENDPOINT_KEY", "free-ping")
PAID_ENDPOINT_KEY = os.getenv("PAID_ENDPOINT_KEY", "paid-summary")
FREE_PAYLOAD: dict[str, object] = {
    "message": "hello from the minimal consumer example",
}
PAID_PAYLOAD: dict[str, object] = {
    "message": "Summarize the request without running the paid x402 settlement flow.",
}

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("minimal_consumer")


async def main() -> None:
    consumer_private_key = get_required_private_key(
        "CONSUMER_PRIVATE_KEY",
        purpose="the minimal consumer example",
    )
    consumer_wallet_address, provider_wallet_address = ensure_distinct_wallets(
        primary_private_key=consumer_private_key,
        primary_env_name="CONSUMER_PRIVATE_KEY",
        secondary_private_key=os.getenv("PROVIDER_PRIVATE_KEY"),
        secondary_env_name="PROVIDER_PRIVATE_KEY",
    )

    logger.info("API base URL: %s", API_BASE_URL)
    logger.info("Consumer wallet address: %s", consumer_wallet_address)
    if provider_wallet_address is not None:
        logger.info("Provider wallet address: %s", provider_wallet_address)

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        access_token, authenticated_wallet = await authenticate(
            client,
            api_base_url=API_BASE_URL,
            private_key=consumer_private_key,
        )
        logger.info("\nAuthenticated consumer wallet: %s", authenticated_wallet)

        service = await _get_public_service(client)
        _select_service(service, SERVICE_SLUG)
        await _log_public_service_views(client, service_slug=SERVICE_SLUG)
        await _create_quote(client, service_slug=SERVICE_SLUG)
        await _invoke_free(
            client,
            service_slug=SERVICE_SLUG,
            access_token=access_token,
        )
        logger.info("\nThis example stops after free invoke and quote creation.")
        logger.info("Use `examples/client.py` for the full paid x402 settlement flow.")


async def _get_public_service(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get("/v1/services")
    response.raise_for_status()
    services = response.json()
    logger.info("\nPublic services:\n%s", json.dumps(services, indent=2))
    return services


def _select_service(services: list[dict[str, Any]], service_slug: str) -> dict[str, Any]:
    for service in services:
        if service.get("slug") == service_slug:
            logger.info("\nSelected service: %s", service_slug)
            return service
    raise RuntimeError(f"Service {service_slug!r} was not returned by GET /v1/services")


async def _log_public_service_views(
    client: httpx.AsyncClient,
    *,
    service_slug: str,
) -> None:
    for suffix, label in (("", "Detail"), ("/schema", "Schema"), ("/pricing", "Pricing")):
        response = await client.get(f"/v1/services/{service_slug}{suffix}")
        response.raise_for_status()
        logger.info("\n%s:\n%s", label, json.dumps(response.json(), indent=2))


async def _create_quote(
    client: httpx.AsyncClient,
    *,
    service_slug: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/services/{service_slug}/quote",
        json={
            "endpoint_key": PAID_ENDPOINT_KEY,
            "payload": PAID_PAYLOAD,
        },
    )
    response.raise_for_status()
    quote = response.json()
    logger.info("\nQuote:\n%s", json.dumps(quote, indent=2))
    return quote


async def _invoke_free(
    client: httpx.AsyncClient,
    *,
    service_slug: str,
    access_token: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/invoke/{service_slug}",
        headers=build_idempotency_headers(access_token, prefix="example-minimal-consumer"),
        json={
            "endpoint_key": FREE_ENDPOINT_KEY,
            "payload": FREE_PAYLOAD,
        },
    )
    response.raise_for_status()
    body = response.json()
    logger.info("\nFree invoke:\n%s", json.dumps(body, indent=2))
    return body


if __name__ == "__main__":
    asyncio.run(main())
