from __future__ import annotations

import asyncio
import json
import logging

import httpx
from demo_common import (
    authenticate,
    authorization_headers,
    get_required_private_key,
    resolve_api_base_url,
)

API_BASE_URL = resolve_api_base_url()
API_KEY_NAME = "demo-agent-key"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("api_key_client")


async def main() -> None:
    consumer_private_key = get_required_private_key(
        "CONSUMER_PRIVATE_KEY",
        purpose="the API key lifecycle example",
    )

    logger.info("API base URL: %s", API_BASE_URL)

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        access_token, wallet_address = await authenticate(
            client,
            api_base_url=API_BASE_URL,
            private_key=consumer_private_key,
        )
        logger.info("\nAuthenticated wallet: %s", wallet_address)

        created_key = await _create_api_key(client, access_token=access_token)
        await _list_api_keys(client, access_token=access_token)

        plaintext_api_key = created_key["api_key"]
        api_key_id = created_key["id"]

        await _list_invocations_with_api_key(client, api_key=plaintext_api_key)
        await _show_jwt_only_rejection(client, api_key=plaintext_api_key)

        await _revoke_api_key(client, access_token=access_token, api_key_id=api_key_id)
        await _show_revoked_key_rejection(client, api_key=plaintext_api_key)


async def _create_api_key(
    client: httpx.AsyncClient,
    *,
    access_token: str,
) -> dict[str, object]:
    response = await client.post(
        "/v1/auth/api-keys",
        headers=authorization_headers(access_token),
        json={"name": API_KEY_NAME},
    )
    response.raise_for_status()
    body = response.json()
    logger.info("\nCreated API key:\n%s", json.dumps(body, indent=2))
    return body


async def _list_api_keys(
    client: httpx.AsyncClient,
    *,
    access_token: str,
) -> list[dict[str, object]]:
    response = await client.get(
        "/v1/auth/api-keys",
        headers=authorization_headers(access_token),
    )
    response.raise_for_status()
    body = response.json()
    logger.info("\nJWT API key list:\n%s", json.dumps(body, indent=2))
    return body


async def _list_invocations_with_api_key(
    client: httpx.AsyncClient,
    *,
    api_key: str,
) -> list[dict[str, object]]:
    response = await client.get(
        "/v1/invocations",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    response.raise_for_status()
    body = response.json()
    logger.info("\nInvocation list with API key:\n%s", json.dumps(body, indent=2))
    return body


async def _show_jwt_only_rejection(
    client: httpx.AsyncClient,
    *,
    api_key: str,
) -> None:
    response = await client.get(
        "/v1/account/me",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    logger.info("\nJWT-only route with API key returned %s", response.status_code)
    logger.info("JWT-only route body: %s", response.text)


async def _revoke_api_key(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    api_key_id: int,
) -> None:
    response = await client.delete(
        f"/v1/auth/api-keys/{api_key_id}",
        headers=authorization_headers(access_token),
    )
    response.raise_for_status()
    logger.info("\nRevoked API key id=%s", api_key_id)


async def _show_revoked_key_rejection(
    client: httpx.AsyncClient,
    *,
    api_key: str,
) -> None:
    response = await client.get(
        "/v1/invocations",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    logger.info("\nRevoked API key route returned %s", response.status_code)
    logger.info("Revoked API key body: %s", response.text)


if __name__ == "__main__":
    asyncio.run(main())
