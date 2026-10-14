from collections.abc import AsyncIterator

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import auth_headers_for_account, create_account

from app.core.config import get_settings
from app.main import create_app

pytestmark = [pytest.mark.asyncio]


@pytest.fixture
async def rate_limited_client(
    monkeypatch: pytest.MonkeyPatch,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("APP_API_RATE_LIMIT", "1/minute")
    get_settings.cache_clear()

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

    get_settings.cache_clear()


async def test_global_rate_limit_applies_to_v1_routes(rate_limited_client: AsyncClient) -> None:
    first = await rate_limited_client.get("/v1/services")
    second = await rate_limited_client.get("/v1/services")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["content-type"] == "application/problem+json"
    assert 55 <= int(second.headers["retry-after"]) <= 60
    assert second.json() == {
        "type": "/problems/rate_limited",
        "status": 429,
        "detail": "rate limit exceeded",
    }


async def test_health_route_is_exempt_from_global_rate_limit(
    rate_limited_client: AsyncClient,
) -> None:
    first = await rate_limited_client.get("/health")
    second = await rate_limited_client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"status": "ok"}
    assert second.json() == {"status": "ok"}


async def test_an_authenticated_caller_is_counted_by_account_not_by_client_address(
    rate_limited_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_account(db_session_factory)
    headers = await auth_headers_for_account(db_session_factory, account_id=account_id)

    anonymous = await rate_limited_client.get("/v1/services")
    first = await rate_limited_client.get("/v1/services", headers=headers)
    second = await rate_limited_client.get("/v1/services", headers=headers)

    # All three come from one client address, whose own limit the first request used up.
    assert [anonymous.status_code, first.status_code, second.status_code] == [200, 200, 429]


async def test_a_caller_with_an_invalid_token_is_counted_by_client_address(
    rate_limited_client: AsyncClient,
) -> None:
    invalid = await rate_limited_client.get(
        "/v1/services",
        headers={"Authorization": "Bearer not-a-token"},
    )
    anonymous = await rate_limited_client.get("/v1/services")

    assert [invalid.status_code, anonymous.status_code] == [200, 429]
