import pytest
from httpx2 import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record
from tests.helpers.auth import api_key_headers_for_account, auth_headers_for_account_id

SIGNING_SECRET_PATH = "/v1/provider/signing-secret"
DOMAIN_VERIFICATION_PATH = "/v1/provider/domain-verification"


async def test_a_signing_secret_is_shown_once_and_never_by_its_status(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)

    issued = await async_client.post(SIGNING_SECRET_PATH, headers=headers)
    status = await async_client.get(SIGNING_SECRET_PATH, headers=headers)

    assert issued.status_code == 201
    assert set(issued.json()) == {"secret", "issued_at"}
    assert issued.json()["secret"].startswith("amp_sig_")
    assert status.status_code == 200
    assert status.json() == {"issued_at": issued.json()["issued_at"]}


async def test_issuing_again_replaces_the_signing_secret(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)
    first = await async_client.post(SIGNING_SECRET_PATH, headers=headers)

    second = await async_client.post(SIGNING_SECRET_PATH, headers=headers)

    assert second.status_code == 201
    assert second.json()["secret"] != first.json()["secret"]


async def test_the_signing_secret_status_without_a_secret_is_not_found(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    response = await async_client.get(
        SIGNING_SECRET_PATH,
        headers=auth_headers_for_account_id(account_id),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "the account has no signing secret"


@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_signing_secret_routes_refuse_api_keys(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    method: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = await api_key_headers_for_account(db_session_factory, account_id=account_id)

    response = await async_client.request(method, SIGNING_SECRET_PATH, headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "jwt authentication required"


async def test_the_domain_verification_record_is_per_account_and_accepts_an_api_key(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    other_account_id = await create_provider_account_record(db_session_factory)

    with_jwt = await async_client.get(
        DOMAIN_VERIFICATION_PATH,
        headers=auth_headers_for_account_id(account_id),
    )
    with_api_key = await async_client.get(
        DOMAIN_VERIFICATION_PATH,
        headers=await api_key_headers_for_account(db_session_factory, account_id=account_id),
    )
    other = await async_client.get(
        DOMAIN_VERIFICATION_PATH,
        headers=auth_headers_for_account_id(other_account_id),
    )

    assert with_jwt.status_code == 200
    assert with_jwt.json()["record_label"] == "_agent-marketplace"
    assert with_jwt.json()["record_value"].startswith("agent-marketplace-verification=")
    assert with_api_key.json() == with_jwt.json()
    assert other.json()["record_value"] != with_jwt.json()["record_value"]
