import pytest
from httpx import AsyncClient
from tests.fixtures.domain import (
    AdminAccountFactory,
    ConsumerAccountFactory,
    ProviderAccountFactory,
    ServiceFactory,
)
from tests.helpers.auth import auth_headers_for_account_id


def _auth_headers(account_id: int) -> dict[str, str]:
    return auth_headers_for_account_id(account_id)


@pytest.mark.asyncio
async def test_suspend_route_records_action(
    async_client: AsyncClient,
    admin_account_factory: AdminAccountFactory,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
) -> None:
    actor_account_id = await admin_account_factory()
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="admin-target",
        description=None,
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
    admin_account_factory: AdminAccountFactory,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
) -> None:
    actor_account_id = await admin_account_factory()
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="restore-target",
        description=None,
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
    admin_account_factory: AdminAccountFactory,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
) -> None:
    actor_account_id = await admin_account_factory()
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="history-target",
        description=None,
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


@pytest.mark.asyncio
async def test_admin_routes_reject_non_admin_user(
    async_client: AsyncClient,
    consumer_account_factory: ConsumerAccountFactory,
) -> None:
    non_admin_id = await consumer_account_factory(display_name="User")
    headers = _auth_headers(non_admin_id)

    suspend = await async_client.post(
        "/v1/admin/services/1/suspend",
        headers=headers,
        json={"reason": "spam"},
    )
    assert suspend.status_code == 403

    restore = await async_client.post(
        "/v1/admin/services/1/restore",
        headers=headers,
        json={"reason": "fixed"},
    )
    assert restore.status_code == 403

    delist = await async_client.post(
        "/v1/admin/services/1/delist",
        headers=headers,
        json={"reason": "policy"},
    )
    assert delist.status_code == 403

    actions = await async_client.get(
        "/v1/admin/moderation/actions",
        headers=headers,
        params={"service_id": 1},
    )
    assert actions.status_code == 403


@pytest.mark.asyncio
async def test_suspend_rejects_empty_reason(
    async_client: AsyncClient,
    admin_account_factory: AdminAccountFactory,
) -> None:
    admin_id = await admin_account_factory()

    response = await async_client.post(
        "/v1/admin/services/1/suspend",
        headers=_auth_headers(admin_id),
        json={"reason": ""},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_suspend_rejects_whitespace_only_reason(
    async_client: AsyncClient,
    admin_account_factory: AdminAccountFactory,
) -> None:
    admin_id = await admin_account_factory()

    response = await async_client.post(
        "/v1/admin/services/1/suspend",
        headers=_auth_headers(admin_id),
        json={"reason": "   "},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_suspend_nonexistent_service_returns_404(
    async_client: AsyncClient,
    admin_account_factory: AdminAccountFactory,
) -> None:
    admin_id = await admin_account_factory()

    response = await async_client.post(
        "/v1/admin/services/999999/suspend",
        headers=_auth_headers(admin_id),
        json={"reason": "spam"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_restore_clear_service_returns_409(
    async_client: AsyncClient,
    admin_account_factory: AdminAccountFactory,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
) -> None:
    admin_id = await admin_account_factory()
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="clear-service",
        description=None,
    )

    response = await async_client.post(
        f"/v1/admin/services/{service_id}/restore",
        headers=_auth_headers(admin_id),
        json={"reason": "not needed"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": f"cannot restore service {service_id} from clear"}


@pytest.mark.asyncio
async def test_delist_route_records_action(
    async_client: AsyncClient,
    admin_account_factory: AdminAccountFactory,
    provider_account_factory: ProviderAccountFactory,
    service_factory: ServiceFactory,
) -> None:
    admin_id = await admin_account_factory()
    provider_account_id = await provider_account_factory()
    service_id = await service_factory(
        provider_account_id=provider_account_id,
        slug="delist-target",
        description=None,
    )

    response = await async_client.post(
        f"/v1/admin/services/{service_id}/delist",
        headers=_auth_headers(admin_id),
        json={"reason": "policy violation"},
    )

    assert response.status_code == 201
    assert response.json()["service_id"] == service_id
    assert response.json()["action"] == "delist"
    assert response.json()["reason"] == "policy violation"
