from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record
from tests.helpers.auth import api_key_headers_for_account, auth_headers_for_account_id

from app.db.models import ProviderSigningSecret

SIGNING_SECRET_PATH = "/v1/provider/signing-secret"
ROTATE_PATH = "/v1/provider/signing-secret/rotate"
DOMAIN_VERIFICATION_PATH = "/v1/provider/domain-verification"


async def test_a_signing_secret_is_returned_once_and_never_by_its_status(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)

    created = await async_client.post(SIGNING_SECRET_PATH, headers=headers)
    status = await async_client.get(SIGNING_SECRET_PATH, headers=headers)

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    assert set(created.json()) == {"secret", "issued_at", "previous_expires_at"}
    assert created.json()["secret"].startswith("amp_sig_")
    assert created.json()["previous_expires_at"] is None
    assert status.status_code == 200
    assert status.json() == {
        "issued_at": created.json()["issued_at"],
        "previous_expires_at": None,
    }


async def test_creating_a_second_signing_secret_is_a_conflict_problem(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)
    await async_client.post(SIGNING_SECRET_PATH, headers=headers)

    response = await async_client.post(SIGNING_SECRET_PATH, headers=headers)

    assert response.status_code == 409
    assert response.json() == {
        "type": "/problems/conflict",
        "status": 409,
        "detail": "the account already has a signing secret; rotate it instead",
    }


async def test_rotating_returns_the_new_secret_and_the_old_ones_grace_deadline(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)
    created = await async_client.post(SIGNING_SECRET_PATH, headers=headers)

    rotated = await async_client.post(ROTATE_PATH, headers=headers)
    status = await async_client.get(SIGNING_SECRET_PATH, headers=headers)

    assert rotated.status_code == 200
    assert rotated.headers["cache-control"] == "no-store"
    assert rotated.json()["secret"].startswith("amp_sig_")
    assert rotated.json()["secret"] != created.json()["secret"]
    assert rotated.json()["previous_expires_at"] is not None
    assert status.json() == {
        "issued_at": rotated.json()["issued_at"],
        "previous_expires_at": rotated.json()["previous_expires_at"],
    }


async def test_a_rotation_retried_with_its_idempotency_key_returns_the_same_secret(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)
    await async_client.post(SIGNING_SECRET_PATH, headers=headers)
    keyed = {**headers, "Idempotency-Key": "8a1f7c52-rotation"}

    rotated = await async_client.post(ROTATE_PATH, headers=keyed)
    retried = await async_client.post(ROTATE_PATH, headers=keyed)

    assert rotated.status_code == 200
    assert retried.status_code == 200
    assert retried.headers["cache-control"] == "no-store"
    assert retried.json() == rotated.json()


async def test_a_rotation_retried_after_the_replay_window_is_a_conflict_problem(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)
    await async_client.post(SIGNING_SECRET_PATH, headers=headers)
    keyed = {**headers, "Idempotency-Key": "8a1f7c52-rotation"}
    await async_client.post(ROTATE_PATH, headers=keyed)
    async with db_session_factory.begin() as session:
        await session.execute(
            update(ProviderSigningSecret)
            .where(ProviderSigningSecret.account_id == account_id)
            .values(issued_at=ProviderSigningSecret.issued_at - timedelta(minutes=16)),
        )
    before = await async_client.get(SIGNING_SECRET_PATH, headers=headers)

    retried = await async_client.post(ROTATE_PATH, headers=keyed)

    assert retried.status_code == 409
    assert retried.json()["type"] == "/problems/conflict"
    assert "GET /v1/provider/signing-secret" in retried.json()["detail"]
    assert "secret" not in set(retried.json())
    assert (await async_client.get(SIGNING_SECRET_PATH, headers=headers)).json() == before.json()


@pytest.mark.parametrize("idempotency_key", ["", "k" * 256], ids=["empty", "too_long"])
async def test_a_rotation_rejects_a_malformed_idempotency_key(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    idempotency_key: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)
    await async_client.post(SIGNING_SECRET_PATH, headers=headers)

    response = await async_client.post(
        ROTATE_PATH,
        headers={**headers, "Idempotency-Key": idempotency_key},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["header", "Idempotency-Key"]


@pytest.mark.parametrize(
    ("method", "path", "detail"),
    [
        ("POST", ROTATE_PATH, "the account has no signing secret; create one first"),
        ("GET", SIGNING_SECRET_PATH, "the account has no signing secret"),
    ],
    ids=["rotate", "status"],
)
async def test_signing_secret_routes_without_a_secret_are_not_found(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    method: str,
    path: str,
    detail: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    response = await async_client.request(
        method,
        path,
        headers=auth_headers_for_account_id(account_id),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == detail


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", SIGNING_SECRET_PATH), ("POST", ROTATE_PATH), ("GET", SIGNING_SECRET_PATH)],
    ids=["create", "rotate", "status"],
)
async def test_signing_secret_routes_refuse_api_keys(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    method: str,
    path: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = await api_key_headers_for_account(db_session_factory, account_id=account_id)

    response = await async_client.request(method, path, headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "jwt authentication required"


async def test_the_domain_verification_record_is_stable_and_differs_per_account(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    other_account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)

    first = await async_client.post(DOMAIN_VERIFICATION_PATH, headers=headers)
    second = await async_client.post(DOMAIN_VERIFICATION_PATH, headers=headers)
    other = await async_client.post(
        DOMAIN_VERIFICATION_PATH,
        headers=auth_headers_for_account_id(other_account_id),
    )

    assert first.status_code == 200
    assert first.json()["record_label"] == "_agent-marketplace"
    assert first.json()["record_value"].startswith("agent-marketplace-verification=")
    assert second.json() == first.json()
    assert other.json()["record_value"] != first.json()["record_value"]


async def test_the_domain_verification_record_accepts_an_api_key(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    with_jwt = await async_client.post(
        DOMAIN_VERIFICATION_PATH,
        headers=auth_headers_for_account_id(account_id),
    )

    with_api_key = await async_client.post(
        DOMAIN_VERIFICATION_PATH,
        headers=await api_key_headers_for_account(db_session_factory, account_id=account_id),
    )

    assert with_api_key.status_code == 200
    assert with_api_key.json() == with_jwt.json()
