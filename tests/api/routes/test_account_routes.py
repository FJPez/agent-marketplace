from datetime import UTC, datetime

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from httpx import AsyncClient


def _build_siwe_message(*, address: str, nonce: str, issued_at: datetime) -> str:
    return "\n".join(
        [
            "testserver wants you to sign in with your Ethereum account:",
            address,
            "",
            "URI: http://testserver",
            "Version: 1",
            "Chain ID: 1",
            f"Nonce: {nonce}",
            f"Issued At: {issued_at.isoformat().replace('+00:00', 'Z')}",
        ],
    )


async def _authenticate(async_client: AsyncClient) -> tuple[str, str]:
    signer = Account.create()
    nonce_response = await async_client.get(
        "/v1/auth/nonce",
        params={"address": signer.address},
    )
    nonce = nonce_response.json()["nonce"]
    issued_at = datetime.now(UTC).replace(microsecond=0)
    message = _build_siwe_message(
        address=signer.address,
        nonce=nonce,
        issued_at=issued_at,
    )
    signed = Account.sign_message(
        signable_message=encode_defunct(text=message),
        private_key=signer.key,
    )
    verify_response = await async_client.post(
        "/v1/auth/verify",
        json={
            "message": message,
            "signature": signed.signature.to_0x_hex(),
        },
    )
    return signer.address, verify_response.json()["access_token"]


@pytest.mark.asyncio
async def test_get_account_me_requires_bearer_token(async_client: AsyncClient) -> None:
    response = await async_client.get("/v1/account/me")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_account_me_returns_authenticated_account(async_client: AsyncClient) -> None:
    address, access_token = await _authenticate(async_client)

    response = await async_client.get(
        "/v1/account/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["wallet_address"] == address
    assert response.json()["display_name"] == "Anonymous"


@pytest.mark.asyncio
async def test_patch_account_me_updates_display_name(async_client: AsyncClient) -> None:
    _, access_token = await _authenticate(async_client)

    response = await async_client.patch(
        "/v1/account/me",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"display_name": "Updated Name"},
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Updated Name"


@pytest.mark.asyncio
async def test_account_me_rejects_api_key_bearer(async_client: AsyncClient) -> None:
    _, access_token = await _authenticate(async_client)
    create_response = await async_client.post(
        "/v1/auth/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "worker-key"},
    )
    api_key = create_response.json()["api_key"]

    response = await async_client.get(
        "/v1/account/me",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_wallet_change_routes_require_jwt_and_complete_rotation(
    async_client: AsyncClient,
) -> None:
    _, access_token = await _authenticate(async_client)
    new_signer = Account.create()

    initiate_response = await async_client.post(
        "/v1/account/wallet",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"wallet_address": new_signer.address},
    )

    assert initiate_response.status_code == 200
    nonce = initiate_response.json()["nonce"]
    issued_at = datetime.now(UTC).replace(microsecond=0)
    message = _build_siwe_message(
        address=new_signer.address,
        nonce=nonce,
        issued_at=issued_at,
    )
    signed = Account.sign_message(
        signable_message=encode_defunct(text=message),
        private_key=new_signer.key,
    )

    confirm_response = await async_client.post(
        "/v1/account/wallet/confirm",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "message": message,
            "signature": signed.signature.to_0x_hex(),
        },
    )

    assert confirm_response.status_code == 200
    body = confirm_response.json()
    assert body["account"]["wallet_address"] == new_signer.address
    assert body["access_token"]
    assert body["refresh_token"]


@pytest.mark.asyncio
async def test_wallet_change_initiate_rejects_invalid_wallet_address(
    async_client: AsyncClient,
) -> None:
    _, access_token = await _authenticate(async_client)

    response = await async_client.post(
        "/v1/account/wallet",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"wallet_address": "not-a-wallet"},
    )

    assert response.status_code == 422
