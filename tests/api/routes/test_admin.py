import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account, ProviderProfile, Service


def _auth_headers(account_id: int) -> dict[str, str]:
    return {"X-Account-Id": str(account_id)}


async def _create_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        return account.id


async def _create_provider_account(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with db_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(ProviderProfile(account_id=account.id, display_name="Provider"))
        return account.id


async def _seed_service(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_account_id: int,
    slug: str,
) -> int:
    async with db_session_factory.begin() as session:
        service = Service(
            provider_account_id=provider_account_id,
            slug=slug,
            name=f"{slug} name",
            summary=f"{slug} summary",
            description=None,
        )
        session.add(service)
        await session.flush()
        return service.id


@pytest.mark.asyncio
async def test_suspend_route_records_action(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    actor_account_id = await _create_account(db_session_factory)
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="admin-target",
    )

    response = await async_client.post(
        f"/v1/admin/services/{service_id}/suspend",
        headers=_auth_headers(actor_account_id),
        json={"reason": "spam"},
    )

    assert response.status_code == 201
    assert response.json()["service_id"] == service_id
    assert response.json()["actor_account_id"] == actor_account_id
    assert response.json()["action"] == "suspend"
    assert response.json()["reason"] == "spam"


@pytest.mark.asyncio
async def test_restore_route_clears_previous_state(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    actor_account_id = await _create_account(db_session_factory)
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="restore-target",
    )

    suspend_response = await async_client.post(
        f"/v1/admin/services/{service_id}/suspend",
        headers=_auth_headers(actor_account_id),
        json={"reason": "spam"},
    )
    assert suspend_response.status_code == 201

    restore_response = await async_client.post(
        f"/v1/admin/services/{service_id}/restore",
        headers=_auth_headers(actor_account_id),
        json={"reason": "remediated"},
    )

    assert restore_response.status_code == 201
    assert restore_response.json()["action"] == "restore"


@pytest.mark.asyncio
async def test_list_actions_route_returns_history_for_service(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    actor_account_id = await _create_account(db_session_factory)
    provider_account_id = await _create_provider_account(db_session_factory)
    service_id = await _seed_service(
        db_session_factory,
        provider_account_id=provider_account_id,
        slug="history-target",
    )

    await async_client.post(
        f"/v1/admin/services/{service_id}/suspend",
        headers=_auth_headers(actor_account_id),
        json={"reason": "spam"},
    )
    await async_client.post(
        f"/v1/admin/services/{service_id}/restore",
        headers=_auth_headers(actor_account_id),
        json={"reason": "fixed"},
    )

    response = await async_client.get(
        "/v1/admin/moderation/actions",
        headers=_auth_headers(actor_account_id),
        params={"service_id": service_id},
    )

    assert response.status_code == 200
    assert [item["action"] for item in response.json()] == ["suspend", "restore"]


@pytest.mark.asyncio
async def test_admin_routes_require_authentication(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/v1/admin/services/1/suspend",
        json={"reason": "spam"},
    )

    assert response.status_code == 401
