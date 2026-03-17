from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
from demo_common import (
    authenticate,
    authorization_headers,
    build_idempotency_headers,
    ensure_distinct_wallets,
    get_required_private_key,
    resolve_api_base_url,
)

API_BASE_URL = resolve_api_base_url()

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("provider_example_client")


async def main() -> None:
    provider_private_key = get_required_private_key(
        "PROVIDER_PRIVATE_KEY",
        purpose="the provider payout example",
    )
    provider_wallet_address, consumer_wallet_address = ensure_distinct_wallets(
        primary_private_key=provider_private_key,
        primary_env_name="PROVIDER_PRIVATE_KEY",
        secondary_private_key=os.getenv("CONSUMER_PRIVATE_KEY"),
        secondary_env_name="CONSUMER_PRIVATE_KEY",
    )

    logger.info("API base URL: %s", API_BASE_URL)
    logger.info("Provider wallet address: %s", provider_wallet_address)
    if consumer_wallet_address is not None:
        logger.info("Consumer wallet address: %s", consumer_wallet_address)

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        access_token, authenticated_wallet = await authenticate(
            client,
            api_base_url=API_BASE_URL,
            private_key=provider_private_key,
        )
        logger.info("\nAuthenticated provider wallet: %s", authenticated_wallet)
        await _list_payouts(
            client,
            access_token=access_token,
            label="Provider payouts before request",
        )
        await _request_payouts(client, access_token=access_token)
        await _list_payouts(
            client,
            access_token=access_token,
            label="Provider payouts after request",
        )


async def _list_payouts(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    label: str,
) -> dict[str, Any]:
    response = await client.get(
        "/v1/provider/payouts",
        headers=authorization_headers(access_token),
    )
    response.raise_for_status()
    body = response.json()
    logger.info("\n%s:\n%s", label, json.dumps(body, indent=2))
    return body


async def _request_payouts(
    client: httpx.AsyncClient,
    *,
    access_token: str,
) -> dict[str, Any] | None:
    response = await client.post(
        "/v1/provider/payouts",
        headers=build_idempotency_headers(access_token, prefix="example-provider-payout"),
    )
    if response.status_code == 409:
        logger.info("\nProvider payout request returned 409:\n%s", response.text)
        return None

    response.raise_for_status()
    body = response.json()
    logger.info("\nProvider payout request:\n%s", json.dumps(body, indent=2))
    return body


if __name__ == "__main__":
    asyncio.run(main())
