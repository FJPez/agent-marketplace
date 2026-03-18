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
    ensure_distinct_wallets,
    get_required_private_key,
    resolve_api_base_url,
)

API_BASE_URL = resolve_api_base_url()
SERVICE_SLUG = os.getenv("SERVICE_SLUG", "demo-agent-service")
SERVICE_NAME = os.getenv("SERVICE_NAME", "Demo Agent Service")
SERVICE_SUMMARY = os.getenv(
    "SERVICE_SUMMARY",
    "Free and paid demo endpoints for manual marketplace testing.",
)
SERVICE_DESCRIPTION = os.getenv(
    "SERVICE_DESCRIPTION",
    "A seeded demo service for discovery, quoting, free invoke, and paid invoke flows.",
)
UPSTREAM_BASE_URL = os.getenv("APP_DEMO_UPSTREAM_BASE_URL", "http://127.0.0.1:9000")
FREE_ENDPOINT_KEY = os.getenv("FREE_ENDPOINT_KEY", "free-ping")
PAID_ENDPOINT_KEY = os.getenv("PAID_ENDPOINT_KEY", "paid-summary")
FREE_UPSTREAM_PATH = os.getenv("APP_DEMO_FREE_UPSTREAM_PATH", "/free-ping")
PAID_UPSTREAM_PATH = os.getenv("APP_DEMO_PAID_UPSTREAM_PATH", "/paid-summary")
FREE_ENDPOINT_TIMEOUT_SECONDS = int(os.getenv("FREE_ENDPOINT_TIMEOUT_SECONDS", "30"))
PAID_ENDPOINT_TIMEOUT_SECONDS = int(os.getenv("PAID_ENDPOINT_TIMEOUT_SECONDS", "30"))
PAID_ENDPOINT_AMOUNT_MINOR = int(os.getenv("PAID_ENDPOINT_AMOUNT_MINOR", "250"))
PAID_ENDPOINT_CURRENCY = os.getenv("PAID_ENDPOINT_CURRENCY", "USD")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("provider_publish")

_DEFAULT_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
    },
    "required": ["message"],
    "additionalProperties": False,
}

_FREE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "mode": {"type": "string"},
    },
    "required": ["message", "mode"],
    "additionalProperties": False,
}

_PAID_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "mode": {"type": "string"},
    },
    "required": ["summary", "mode"],
    "additionalProperties": False,
}


async def main() -> None:
    provider_private_key = get_required_private_key(
        "PROVIDER_PRIVATE_KEY",
        purpose="the provider publish example",
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
    logger.info("Demo upstream base URL: %s", UPSTREAM_BASE_URL)

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
        access_token, authenticated_wallet = await authenticate(
            client,
            api_base_url=API_BASE_URL,
            private_key=provider_private_key,
        )
        logger.info("\nAuthenticated provider wallet: %s", authenticated_wallet)

        service = await _ensure_service(client, access_token=access_token)
        service = await _upsert_free_endpoint(client, access_token=access_token, service=service)
        service = await _upsert_paid_endpoint(client, access_token=access_token, service=service)
        published = await _publish_service(
            client, access_token=access_token, service_id=service["id"]
        )

        logger.info("\nPublished service:\n%s", json.dumps(published, indent=2))
        logger.info("Service slug: %s", SERVICE_SLUG)
        logger.info("Free endpoint: %s -> %s", FREE_ENDPOINT_KEY, FREE_UPSTREAM_PATH)
        logger.info("Paid endpoint: %s -> %s", PAID_ENDPOINT_KEY, PAID_UPSTREAM_PATH)
        logger.info("Next step: run `uv run python examples/minimal_consumer.py`.")


async def _ensure_service(
    client: httpx.AsyncClient,
    *,
    access_token: str,
) -> dict[str, Any]:
    services = await _list_provider_services(client, access_token=access_token)
    existing = _find_service(services, SERVICE_SLUG)
    request_body = {
        "slug": SERVICE_SLUG,
        "name": SERVICE_NAME,
        "summary": SERVICE_SUMMARY,
        "description": SERVICE_DESCRIPTION,
    }

    if existing is None:
        response = await client.post(
            "/v1/provider/services",
            headers=authorization_headers(access_token),
            json=request_body,
        )
        response.raise_for_status()
        service = response.json()
        logger.info("\nCreated service:\n%s", json.dumps(service, indent=2))
        return service

    response = await client.patch(
        f"/v1/provider/services/{existing['id']}",
        headers=authorization_headers(access_token),
        json=request_body,
    )
    response.raise_for_status()
    service = response.json()
    logger.info("\nUpdated service:\n%s", json.dumps(service, indent=2))
    return service


async def _upsert_free_endpoint(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    service: dict[str, Any],
) -> dict[str, Any]:
    existing = _find_endpoint(service, FREE_ENDPOINT_KEY)
    request_body = {
        "key": FREE_ENDPOINT_KEY,
        "name": "Free Ping",
        "summary": "A lightweight free endpoint for local demo traffic.",
        "description": "Echoes a simple pong-style response from the mock upstream.",
        "access_mode": "free",
        "request_schema": _DEFAULT_REQUEST_SCHEMA,
        "response_schema": _FREE_RESPONSE_SCHEMA,
        "timeout_seconds": FREE_ENDPOINT_TIMEOUT_SECONDS,
        "is_enabled": True,
        "pricing": None,
    }

    if existing is None:
        response = await client.post(
            f"/v1/provider/services/{service['id']}/endpoints",
            headers=authorization_headers(access_token),
            json=request_body,
        )
    else:
        response = await client.patch(
            f"/v1/provider/endpoints/{existing['id']}",
            headers=authorization_headers(access_token),
            json=request_body,
        )
    response.raise_for_status()
    endpoint = response.json()
    await _upsert_endpoint_upstream(
        client,
        access_token=access_token,
        endpoint_id=endpoint["id"],
        base_url=UPSTREAM_BASE_URL,
        path=FREE_UPSTREAM_PATH,
    )
    logger.info("\nConfigured free endpoint:\n%s", json.dumps(endpoint, indent=2))
    return await _refresh_service(client, access_token=access_token, service_id=service["id"])


async def _upsert_paid_endpoint(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    service: dict[str, Any],
) -> dict[str, Any]:
    existing = _find_endpoint(service, PAID_ENDPOINT_KEY)
    request_body = {
        "key": PAID_ENDPOINT_KEY,
        "name": "Paid Summary",
        "summary": "A paid endpoint that returns a compact summary.",
        "description": "Uses x402 payment enforcement for paid invocations.",
        "access_mode": "paid",
        "request_schema": _DEFAULT_REQUEST_SCHEMA,
        "response_schema": _PAID_RESPONSE_SCHEMA,
        "timeout_seconds": PAID_ENDPOINT_TIMEOUT_SECONDS,
        "is_enabled": True,
        "pricing": {
            "pricing_type": "fixed_per_call",
            "amount_minor": PAID_ENDPOINT_AMOUNT_MINOR,
            "currency": PAID_ENDPOINT_CURRENCY,
        },
    }

    if existing is None:
        response = await client.post(
            f"/v1/provider/services/{service['id']}/endpoints",
            headers=authorization_headers(access_token),
            json=request_body,
        )
    else:
        response = await client.patch(
            f"/v1/provider/endpoints/{existing['id']}",
            headers=authorization_headers(access_token),
            json=request_body,
        )
    response.raise_for_status()
    endpoint = response.json()
    await _upsert_endpoint_upstream(
        client,
        access_token=access_token,
        endpoint_id=endpoint["id"],
        base_url=UPSTREAM_BASE_URL,
        path=PAID_UPSTREAM_PATH,
    )
    logger.info("\nConfigured paid endpoint:\n%s", json.dumps(endpoint, indent=2))
    return await _refresh_service(client, access_token=access_token, service_id=service["id"])


async def _upsert_endpoint_upstream(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    endpoint_id: int,
    base_url: str,
    path: str,
) -> None:
    response = await client.put(
        f"/v1/provider/endpoints/{endpoint_id}/upstream",
        headers=authorization_headers(access_token),
        json={
            "base_url": base_url,
            "path": path,
            "http_method": "POST",
            "config": {
                "auth": {
                    "type": "hmac_sha256",
                    "key_id": "demo-key",
                    "secret": "demo-secret",
                },
            },
        },
    )
    response.raise_for_status()


async def _publish_service(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    service_id: int,
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/provider/services/{service_id}/publish",
        headers=authorization_headers(access_token),
    )
    response.raise_for_status()
    return response.json()


async def _list_provider_services(
    client: httpx.AsyncClient,
    *,
    access_token: str,
) -> list[dict[str, Any]]:
    response = await client.get(
        "/v1/provider/services",
        headers=authorization_headers(access_token),
    )
    response.raise_for_status()
    services = response.json()
    logger.info("\nProvider services:\n%s", json.dumps(services, indent=2))
    return services


async def _refresh_service(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    service_id: int,
) -> dict[str, Any]:
    response = await client.get(
        f"/v1/provider/services/{service_id}",
        headers=authorization_headers(access_token),
    )
    response.raise_for_status()
    return response.json()


def _find_service(services: list[dict[str, Any]], slug: str) -> dict[str, Any] | None:
    for service in services:
        if service.get("slug") == slug:
            return service
    return None


def _find_endpoint(service: dict[str, Any], endpoint_key: str) -> dict[str, Any] | None:
    for endpoint in service.get("endpoints", []):
        if endpoint.get("key") == endpoint_key:
            return endpoint
    return None


if __name__ == "__main__":
    asyncio.run(main())
