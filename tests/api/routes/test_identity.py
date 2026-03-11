import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account, ConsumerProfile, ProviderProfile


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


async def _seed_provider_profile(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
    display_name: str,
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            ProviderProfile(account_id=account_id, display_name=display_name),
        )


async def _seed_consumer_profile(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
    display_name: str,
) -> None:
    async with db_session_factory.begin() as session:
        session.add(
            ConsumerProfile(account_id=account_id, display_name=display_name),
        )


@pytest.mark.asyncio
async def test_provider_routes_require_x_account_id_header(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get("/v1/providers/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "X-Account-Id header is required"}


@pytest.mark.parametrize("header_value", ["abc", "0", "-9"])
@pytest.mark.asyncio
async def test_provider_routes_reject_invalid_account_id_header(
    async_client: AsyncClient,
    header_value: str,
) -> None:
    response = await async_client.get(
        "/v1/providers/me",
        headers={"X-Account-Id": header_value},
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "X-Account-Id must be a positive integer",
    }


@pytest.mark.asyncio
async def test_provider_routes_reject_unknown_account(
    async_client: AsyncClient,
) -> None:
    response = await async_client.get(
        "/v1/providers/me",
        headers={"X-Account-Id": "999"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "authenticated account does not exist"}


@pytest.mark.asyncio
async def test_provider_routes_accept_known_account_before_profile_exists(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)

    response = await async_client.get(
        "/v1/providers/me",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_provider_profile_returns_created_profile(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)

    response = await async_client.post(
        "/v1/providers",
        headers=_auth_headers(account_id),
        json={"display_name": "Alpha Provider"},
    )

    assert response.status_code == 201
    assert response.json()["account_id"] == account_id
    assert response.json()["display_name"] == "Alpha Provider"
    assert response.json()["created_at"]


@pytest.mark.asyncio
async def test_create_provider_profile_rejects_duplicates(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)
    await _seed_provider_profile(
        db_session_factory,
        account_id=account_id,
        display_name="Existing Provider",
    )

    response = await async_client.post(
        "/v1/providers",
        headers=_auth_headers(account_id),
        json={"display_name": "Duplicate Provider"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "provider profile already exists"}


@pytest.mark.asyncio
async def test_get_provider_profile_returns_current_profile(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)
    await _seed_provider_profile(
        db_session_factory,
        account_id=account_id,
        display_name="Readable Provider",
    )

    response = await async_client.get(
        "/v1/providers/me",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == account_id
    assert response.json()["display_name"] == "Readable Provider"


@pytest.mark.asyncio
async def test_get_provider_profile_returns_not_found_when_missing(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)

    response = await async_client.get(
        "/v1/providers/me",
        headers=_auth_headers(account_id),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "provider profile not found"}


@pytest.mark.asyncio
async def test_patch_provider_profile_updates_display_name(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)
    await _seed_provider_profile(
        db_session_factory,
        account_id=account_id,
        display_name="Old Provider",
    )

    response = await async_client.patch(
        "/v1/providers/me",
        headers=_auth_headers(account_id),
        json={"display_name": "Updated Provider"},
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == account_id
    assert response.json()["display_name"] == "Updated Provider"


@pytest.mark.asyncio
async def test_patch_provider_profile_returns_not_found_when_missing(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)

    response = await async_client.patch(
        "/v1/providers/me",
        headers=_auth_headers(account_id),
        json={"display_name": "Updated Provider"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "provider profile not found"}


@pytest.mark.asyncio
async def test_patch_provider_profile_rejects_empty_payload(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)
    await _seed_provider_profile(
        db_session_factory,
        account_id=account_id,
        display_name="Existing Provider",
    )

    response = await async_client.patch(
        "/v1/providers/me",
        headers=_auth_headers(account_id),
        json={},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "at least one field must be provided"}


@pytest.mark.asyncio
async def test_patch_provider_profile_rejects_blank_display_name(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)
    await _seed_provider_profile(
        db_session_factory,
        account_id=account_id,
        display_name="Existing Provider",
    )

    response = await async_client.patch(
        "/v1/providers/me",
        headers=_auth_headers(account_id),
        json={"display_name": "   "},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "display_name"


@pytest.mark.asyncio
async def test_create_consumer_profile_returns_created_profile(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)

    response = await async_client.post(
        "/v1/consumers",
        headers=_auth_headers(account_id),
        json={"display_name": "Consumer One"},
    )

    assert response.status_code == 201
    assert response.json()["account_id"] == account_id
    assert response.json()["display_name"] == "Consumer One"
    assert response.json()["created_at"]


@pytest.mark.asyncio
async def test_create_consumer_profile_rejects_duplicates(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await _create_account(db_session_factory)
    await _seed_consumer_profile(
        db_session_factory,
        account_id=account_id,
        display_name="Existing Consumer",
    )

    response = await async_client.post(
        "/v1/consumers",
        headers=_auth_headers(account_id),
        json={"display_name": "Consumer Two"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "consumer profile already exists"}
