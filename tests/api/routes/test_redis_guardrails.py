from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

pytestmark = [
    pytest.mark.skipif(
        "TEST_REDIS_URL" not in os.environ,
        reason="TEST_REDIS_URL is not configured",
    ),
    pytest.mark.xdist_group("redis"),
]


@pytest.fixture
async def redis_app_clients(
    monkeypatch: pytest.MonkeyPatch,
    clean_database: None,
    test_redis_url: str,
) -> AsyncIterator[tuple[FastAPI, AsyncClient, FastAPI, AsyncClient]]:
    monkeypatch.setenv("APP_REDIS_URL", test_redis_url)
    monkeypatch.setenv("APP_API_RATE_LIMIT", "1/minute")
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


async def test_health_ready_passes_when_redis_is_available(
    redis_app_clients: tuple[FastAPI, AsyncClient, FastAPI, AsyncClient],
) -> None:
    _, first_client, _, _ = redis_app_clients

    response = await first_client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
