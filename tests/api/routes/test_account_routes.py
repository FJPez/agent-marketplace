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
