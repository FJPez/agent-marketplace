from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from tests.helpers.auth import auth_headers_for_account_id

from app.core.config import get_settings
from app.core.enums import AccessMode, ServiceLifecycle
from app.core.lifespan import get_app_state
from app.db.models import Account, ProviderUpstream, Service, ServiceEndpoint, ServiceRevision
from app.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.skipif(
    "TEST_REDIS_URL" not in os.environ,
    reason="TEST_REDIS_URL is not configured",
)


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(display_name="Provider")
        session.add(account)
        await session.flush()
        return account.id


async def _create_consumer_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account(display_name="Consumer")
        session.add(account)
        await session.flush()
        return account.id


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
) -> int:
    async with db_session_factory.begin() as session:
        service = Service(
            provider_account_id=provider_account_id,
            slug="redis-guardrail-service",
            name="Redis Guardrail Service",
            summary="summary",
            description=None,
            lifecycle=ServiceLifecycle.ACTIVE,
        )
        session.add(service)
        await session.flush()
        revision = ServiceRevision(
            service_id=service.id,
            revision_number=1,
            change_token="d" * 64,
            snapshot={"slug": service.slug},
        )
        session.add(revision)
        await session.flush()
        service.current_revision_id = revision.id
        service.current_change_token = revision.change_token
        return service.id


async def _seed_endpoint(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    service_id: int,
) -> None:
    async with db_session_factory.begin() as session:
        endpoint = ServiceEndpoint(
            service_id=service_id,
            key="translate",
            name="Translate",
            summary="Translate text",
            description=None,
            access_mode=AccessMode.FREE,
            request_schema={"type": "object"},
            response_schema={"type": "object"},
            timeout_seconds=30,
            is_enabled=True,
        )
        session.add(endpoint)
        await session.flush()
        session.add(
            ProviderUpstream(
                endpoint_id=endpoint.id,
                base_url="https://provider.internal",
                path="/invoke",
                http_method="POST",
                config={
                    "auth": {
                        "type": "hmac_sha256",
                        "key_id": "gateway-key",
                        "secret": "super-secret",
                    },
                },
            )
        )


@dataclass
class _SlowHttpClient:
    started: asyncio.Event
    release: asyncio.Event
    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        **kwargs: int,
    ) -> Response:
        self.started.set()
        await self.release.wait()
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
                "kwargs": kwargs,
            }
        )
        return Response(200, json={"result": "ok"})

    async def aclose(self) -> None:
        return None


@pytest.fixture
async def redis_app_clients(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
    test_redis_url: str,
) -> AsyncIterator[tuple[FastAPI, AsyncClient, FastAPI, AsyncClient]]:
    _ = migrated_database
    monkeypatch.setenv("APP_REDIS_URL", test_redis_url)
    monkeypatch.setenv("APP_API_RATE_LIMIT", "1/minute")
    monkeypatch.setenv("APP_INVOKE_RATE_LIMIT", "10/minute")
    monkeypatch.setenv("APP_QUOTE_RATE_LIMIT", "10/minute")
    monkeypatch.setenv("APP_INVOKE_PAYLOAD_MAX_BYTES", "4096")
    get_settings.cache_clear()

    first_app = create_app()
    second_app = create_app()

    async with (
        first_app.router.lifespan_context(first_app),
        second_app.router.lifespan_context(second_app),
    ):
        first_transport = ASGITransport(app=first_app)
        second_transport = ASGITransport(app=second_app)
        async with (
            AsyncClient(transport=first_transport, base_url="http://testserver") as first_client,
            AsyncClient(transport=second_transport, base_url="http://testserver") as second_client,
        ):
            yield first_app, first_client, second_app, second_client

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_health_ready_passes_when_redis_is_available(
    redis_app_clients: tuple[FastAPI, AsyncClient, FastAPI, AsyncClient],
) -> None:
    _, first_client, _, _ = redis_app_clients

    response = await first_client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_redis_global_rate_limit_is_shared_across_app_instances(
    redis_app_clients: tuple[FastAPI, AsyncClient, FastAPI, AsyncClient],
) -> None:
    _, first_client, _, second_client = redis_app_clients
    headers = {"Authorization": "Bearer shared-test-token"}

    first = await first_client.get("/v1/services", headers=headers)
    second = await second_client.get("/v1/services", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "rate limit exceeded"}


@pytest.mark.asyncio
async def test_redis_invoke_lock_is_shared_across_app_instances(
    redis_app_clients: tuple[FastAPI, AsyncClient, FastAPI, AsyncClient],
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_app, first_client, _, second_client = redis_app_clients
    provider_account_id = await _create_provider_account(db_session_factory)
    consumer_account_id = await _create_consumer_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
    )
    await _seed_endpoint(db_session_factory, service_id=service_id)

    slow_http_client = _SlowHttpClient()
    get_app_state(first_app).http_client = slow_http_client

    payload = {"endpoint_key": "translate", "payload": {"text": "hello"}, "quote_id": None}
    first_request = asyncio.create_task(
        first_client.post(
            f"/v1/invoke/{service_id}",
            headers=auth_headers_for_account_id(
                consumer_account_id,
                idempotency_key="redis-shared-key",
            ),
            json=payload,
        )
    )
    await slow_http_client.started.wait()

    second = await second_client.post(
        f"/v1/invoke/{service_id}",
        headers=auth_headers_for_account_id(
            consumer_account_id,
            idempotency_key="redis-shared-key",
        ),
        json=payload,
    )

    slow_http_client.release.set()
    first = await first_request

    assert second.status_code == 409
    assert second.json() == {"detail": "request already in progress"}
    assert first.status_code == 200
