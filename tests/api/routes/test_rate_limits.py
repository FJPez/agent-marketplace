from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
async def rate_limited_client(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> AsyncIterator[AsyncClient]:
    _ = migrated_database
    monkeypatch.setenv("APP_API_RATE_LIMIT", "1/minute")
    monkeypatch.setenv("APP_INVOKE_RATE_LIMIT", "10/minute")
    monkeypatch.setenv("APP_QUOTE_RATE_LIMIT", "10/minute")
    get_settings.cache_clear()

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_global_rate_limit_applies_to_v1_routes(rate_limited_client: AsyncClient) -> None:
    first = await rate_limited_client.get("/v1/services")
    second = await rate_limited_client.get("/v1/services")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "rate limit exceeded"}


@pytest.mark.asyncio
async def test_health_route_is_exempt_from_global_rate_limit(
    rate_limited_client: AsyncClient,
) -> None:
    first = await rate_limited_client.get("/health")
    second = await rate_limited_client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"status": "ok"}
    assert second.json() == {"status": "ok"}
