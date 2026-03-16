from datetime import UTC, datetime

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account as AccountModel


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


@pytest.mark.asyncio
async def test_get_auth_nonce_creates_account_for_new_wallet(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    signer = Account.create()

    response = await async_client.get(
        "/v1/auth/nonce",
        params={"address": signer.address.lower()},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["nonce"], str)
    assert body["nonce"]

    async with db_session_factory() as session:
        account = await session.scalar(
            select(AccountModel).where(AccountModel.wallet_address == signer.address),
        )

    assert account is not None
    assert account.display_name == "Anonymous"


@pytest.mark.asyncio
async def test_verify_auth_signature_returns_token_pair_and_account(
    async_client: AsyncClient,
) -> None:
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

    response = await async_client.post(
        "/v1/auth/verify",
        json={
            "message": message,
            "signature": signed.signature.to_0x_hex(),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["account"]["wallet_address"] == signer.address
    assert body["account"]["account_type"] == "human"
    assert body["account"]["display_name"] == "Anonymous"


@pytest.mark.asyncio
async def test_refresh_auth_token_returns_new_access_token(
    async_client: AsyncClient,
) -> None:
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
    refresh_token = verify_response.json()["refresh_token"]

    response = await async_client.post(
        "/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
