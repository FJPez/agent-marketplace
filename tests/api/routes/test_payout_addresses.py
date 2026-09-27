from datetime import datetime, timedelta

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_account.signers.local import LocalAccount
from httpx import AsyncClient
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record
from tests.helpers.auth import api_key_headers_for_account, auth_headers_for_account_id

from app.db.models import PayoutAddressChallenge

CHALLENGE_PATH = "/v1/provider/payout-address/challenge"
PAYOUT_ADDRESS_PATH = "/v1/provider/payout-address"
# The shape of a canonical signature (a low s, v 27), though no key made it.
SIGNATURE = "0x" + "ab" * 32 + "3c" * 32 + "1b"


async def _signed_challenge(
    async_client: AsyncClient,
    headers: dict[str, str],
    wallet: LocalAccount,
) -> str:
    """Request a challenge for the wallet's address and return the wallet's signature of it."""
    challenge = await async_client.post(
        CHALLENGE_PATH,
        headers=headers,
        json={"address": wallet.address, "network": "eip155:84532"},
    )
    signable = encode_typed_data(full_message=challenge.json()["typed_data"])
    return wallet.sign_message(signable).signature.to_0x_hex()


async def test_a_payout_address_is_proven_through_the_api_and_held(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = auth_headers_for_account_id(await create_provider_account_record(db_session_factory))
    wallet = Account.create()

    challenge = await async_client.post(
        CHALLENGE_PATH,
        headers=headers,
        json={"address": wallet.address.lower(), "network": "eip155:84532"},
    )
    typed_data = challenge.json()["typed_data"]
    signature = wallet.sign_message(encode_typed_data(full_message=typed_data)).signature
    proven = await async_client.post(
        PAYOUT_ADDRESS_PATH,
        headers=headers,
        json={"signature": signature.to_0x_hex()},
    )
    current = await async_client.get(PAYOUT_ADDRESS_PATH, headers=headers)

    assert challenge.status_code == 201
    assert typed_data["primaryType"] == "PayoutAddressProof"
    assert typed_data["message"]["payoutAddress"] == wallet.address
    assert typed_data["message"]["nonce"] == challenge.json()["nonce"]
    assert proven.status_code == 201
    body = proven.json()
    assert (body["address"], body["network"], body["status"]) == (
        wallet.address,
        "eip155:84532",
        "pending",
    )
    verified_at = datetime.fromisoformat(body["verified_at"])
    assert datetime.fromisoformat(body["effective_at"]) == verified_at + timedelta(days=1)
    assert current.status_code == 200
    assert current.json() == body


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("POST", CHALLENGE_PATH, {"address": "0x" + "11" * 20, "network": "eip155:84532"}),
        ("POST", PAYOUT_ADDRESS_PATH, {"signature": SIGNATURE}),
        ("GET", PAYOUT_ADDRESS_PATH, None),
    ],
    ids=["challenge", "prove", "read"],
)
async def test_payout_address_routes_take_a_jwt_only(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    method: str,
    path: str,
    json: dict[str, str] | None,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = await api_key_headers_for_account(db_session_factory, account_id=account_id)

    response = await async_client.request(method, path, headers=headers, json=json)

    assert response.status_code == 403
    assert response.json()["detail"] == "jwt authentication required"


async def test_a_mistyped_checksum_in_a_challenge_is_an_invalid_input_problem(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = auth_headers_for_account_id(await create_provider_account_record(db_session_factory))

    response = await async_client.post(
        CHALLENGE_PATH,
        headers=headers,
        json={"address": "0x036cbD53842c5426634e7929541eC2318f3dCF7e", "network": "eip155:84532"},
    )

    assert response.status_code == 422
    assert [(error["loc"], error["msg"]) for error in response.json()["errors"]] == [
        (["body", "address"], "Value error, address has an invalid EIP-55 checksum"),
    ]


async def test_a_signature_by_another_key_is_an_invalid_input_problem(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = auth_headers_for_account_id(await create_provider_account_record(db_session_factory))
    wallet = Account.create()
    challenge = await async_client.post(
        CHALLENGE_PATH,
        headers=headers,
        json={"address": wallet.address, "network": "eip155:84532"},
    )
    signable = encode_typed_data(full_message=challenge.json()["typed_data"])
    signature = Account.create().sign_message(signable).signature.to_0x_hex()

    response = await async_client.post(
        PAYOUT_ADDRESS_PATH,
        headers=headers,
        json={"signature": signature},
    )

    assert response.status_code == 422
    assert response.json() == {
        "type": "/problems/invalid_input",
        "title": "Invalid input",
        "status": 422,
        "detail": f"signature was not made by {wallet.address} over the pending challenge",
    }


@pytest.mark.parametrize(
    ("method", "json", "status", "detail"),
    [
        (
            "POST",
            {"signature": SIGNATURE},
            409,
            "no payout address challenge is pending; request one first",
        ),
        ("GET", None, 404, "no payout address has been proven on eip155:84532"),
    ],
    ids=["prove", "read"],
)
async def test_payout_address_routes_without_a_challenge_or_a_proof(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    method: str,
    json: dict[str, str] | None,
    status: int,
    detail: str,
) -> None:
    headers = auth_headers_for_account_id(await create_provider_account_record(db_session_factory))

    response = await async_client.request(method, PAYOUT_ADDRESS_PATH, headers=headers, json=json)

    assert response.status_code == status
    assert response.json()["detail"] == detail


async def test_a_malformed_signature_is_a_validation_error_and_keeps_the_challenge(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = auth_headers_for_account_id(await create_provider_account_record(db_session_factory))
    wallet = Account.create()
    signature = await _signed_challenge(async_client, headers, wallet)

    malformed = await async_client.post(
        PAYOUT_ADDRESS_PATH,
        headers=headers,
        json={"signature": "0x1234"},
    )
    proven = await async_client.post(
        PAYOUT_ADDRESS_PATH,
        headers=headers,
        json={"signature": signature},
    )

    assert malformed.status_code == 422
    assert malformed.json()["type"] == "/problems/invalid_input"
    assert [(error["loc"], error["type"]) for error in malformed.json()["errors"]] == [
        (["body", "signature"], "string_pattern_mismatch"),
    ]
    assert (proven.status_code, proven.json()["address"]) == (201, wallet.address)


async def test_an_expired_challenge_is_an_invalid_state_problem(
    async_client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    headers = auth_headers_for_account_id(account_id)
    signature = await _signed_challenge(async_client, headers, Account.create())
    async with db_session_factory.begin() as session:
        await session.execute(
            update(PayoutAddressChallenge)
            .where(PayoutAddressChallenge.account_id == account_id)
            .values(expires_at=func.now() - timedelta(seconds=1)),
        )

    response = await async_client.post(
        PAYOUT_ADDRESS_PATH,
        headers=headers,
        json={"signature": signature},
    )

    assert response.status_code == 409
    assert response.json() == {
        "type": "/problems/invalid_state",
        "title": "Invalid state",
        "status": 409,
        "detail": "the payout address challenge has expired; request a new one",
    }
