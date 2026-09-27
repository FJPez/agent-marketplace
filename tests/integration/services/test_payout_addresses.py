import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Self

import pytest
from eth_account import Account
from eth_account.datastructures import SignedMessage
from eth_account.messages import encode_defunct, encode_typed_data
from eth_account.signers.local import LocalAccount
from eth_keys.constants import SECPK1_N
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record
from tests.fixtures.settings import build_service_settings

from app.core.config import Settings
from app.core.errors import InvalidInputError, InvalidStateError, NotFoundError
from app.core.security import verify_siwe_signature
from app.db.models import Account as AccountModel
from app.db.models import PayoutAddress, PayoutAddressChallenge
from app.services import payout_addresses

PAYMENT_NETWORK = "eip155:84532"
PAYOUT_ADDRESS = "0x1111111111111111111111111111111111111111"


def _signed(challenge: PayoutAddressChallenge, wallet: LocalAccount) -> SignedMessage:
    """Sign the challenge's typed data as a wallet does for eth_signTypedData_v4."""
    signable = encode_typed_data(full_message=payout_addresses.proof_typed_data(challenge))
    return wallet.sign_message(signable)


def _sign(challenge: PayoutAddressChallenge, wallet: LocalAccount) -> str:
    return _signed(challenge, wallet).signature.to_0x_hex()


def _encode_signature(r: int, s: int, v: int) -> str:
    return "0x" + r.to_bytes(32).hex() + s.to_bytes(32).hex() + v.to_bytes(1).hex()


async def _request(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
    wallet: LocalAccount,
    *,
    settings: Settings | None = None,
) -> PayoutAddressChallenge:
    settings = settings or build_service_settings()
    async with db_session_factory() as session:
        return await payout_addresses.request_payout_address_challenge(
            session=session,
            settings=settings,
            account_id=account_id,
            address=wallet.address,
            network=settings.payment_network,
        )


async def _prove(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
    signature: str,
) -> PayoutAddress:
    async with db_session_factory() as session:
        return await payout_addresses.prove_payout_address(
            session=session,
            settings=build_service_settings(),
            account_id=account_id,
            signature=signature,
        )


async def _prove_address(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
    wallet: LocalAccount,
    *,
    settings: Settings | None = None,
) -> PayoutAddress:
    challenge = await _request(db_session_factory, account_id, wallet, settings=settings)
    return await _prove(db_session_factory, account_id, _sign(challenge, wallet))


async def _effective(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
    *,
    at: datetime | None,
    network: str = PAYMENT_NETWORK,
) -> str | None:
    async with db_session_factory() as session:
        return await payout_addresses.effective_payout_address(
            session=session,
            account_id=account_id,
            network=network,
            at=at,
        )


async def _database_now(db_session_factory: async_sessionmaker[AsyncSession]) -> datetime:
    async with db_session_factory() as session:
        return (await session.execute(select(func.now()))).scalar_one()


async def _insert_proof(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
    *,
    verified_at: datetime,
    effective_at: datetime,
) -> None:
    """Record a proof directly, as a process with another clock might have."""
    async with db_session_factory.begin() as session:
        session.add(
            PayoutAddress(
                account_id=account_id,
                network=PAYMENT_NETWORK,
                address=PAYOUT_ADDRESS,
                nonce="an-inserted-proofs-nonce",
                signature="0x",
                verified_at=verified_at,
                effective_at=effective_at,
            ),
        )


async def _count_payout_addresses(db_session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with db_session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(PayoutAddress))
    assert count is not None
    return count


async def test_a_proven_address_is_recorded_and_held_before_it_takes_effect(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    challenge = await _request(db_session_factory, account_id, wallet)
    signature = _sign(challenge, wallet)

    proven = await _prove(db_session_factory, account_id, signature)

    assert (proven.address, proven.network, proven.nonce, proven.signature) == (
        wallet.address,
        PAYMENT_NETWORK,
        challenge.nonce,
        signature,
    )
    assert proven.effective_at == proven.verified_at + timedelta(
        seconds=build_service_settings().payout_address_hold_seconds,
    )
    held = await _effective(db_session_factory, account_id, at=proven.verified_at)
    effective = await _effective(db_session_factory, account_id, at=proven.effective_at)
    assert (held, effective) == (None, wallet.address)
    async with db_session_factory() as session:
        latest = await payout_addresses.get_payout_address(
            session=session,
            settings=build_service_settings(),
            account_id=account_id,
        )
        assert latest.id == proven.id
        assert await session.get(PayoutAddressChallenge, account_id) is None


@pytest.mark.parametrize("same_address", [False, True], ids=["changed_address", "same_address"])
async def test_every_proof_holds_payouts_until_its_own_hold_ends(
    db_session_factory: async_sessionmaker[AsyncSession],
    same_address: bool,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    first_wallet = Account.create()
    second_wallet = first_wallet if same_address else Account.create()
    first = await _prove_address(db_session_factory, account_id, first_wallet)
    before_the_change = await _effective(db_session_factory, account_id, at=first.effective_at)

    second = await _prove_address(db_session_factory, account_id, second_wallet)

    held = await _effective(db_session_factory, account_id, at=first.effective_at)
    after_the_hold = await _effective(db_session_factory, account_id, at=second.effective_at)
    assert (before_the_change, held, after_the_hold) == (
        first_wallet.address,
        None,
        second_wallet.address,
    )
    assert await _count_payout_addresses(db_session_factory) == 2


async def test_the_latest_proof_by_id_decides_even_when_its_clock_was_behind(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    first = await _prove_address(db_session_factory, account_id, Account.create())

    # Recorded after the first proof, by a process whose clock was a minute behind.
    await _insert_proof(
        db_session_factory,
        account_id,
        verified_at=first.verified_at - timedelta(minutes=1),
        effective_at=first.effective_at - timedelta(minutes=1),
    )

    async with db_session_factory() as session:
        latest = await payout_addresses.get_payout_address(
            session=session,
            settings=build_service_settings(),
            account_id=account_id,
        )
    effective = await _effective(db_session_factory, account_id, at=first.effective_at)
    assert (latest.address, effective) == (PAYOUT_ADDRESS, PAYOUT_ADDRESS)


async def test_payout_addresses_on_different_networks_are_independent(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    base_mainnet = build_service_settings().model_copy(update={"payment_network": "eip155:8453"})
    on_sepolia = await _prove_address(db_session_factory, account_id, Account.create())
    on_mainnet = await _prove_address(
        db_session_factory,
        account_id,
        Account.create(),
        settings=base_mainnet,
    )

    at = on_mainnet.effective_at
    sepolia = await _effective(db_session_factory, account_id, at=at)
    mainnet = await _effective(db_session_factory, account_id, at=at, network="eip155:8453")
    assert (sepolia, mainnet) == (on_sepolia.address, on_mainnet.address)


async def test_a_login_signature_is_not_a_payout_proof(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    await _request(db_session_factory, account_id, wallet)
    login_message = "testserver wants you to sign in with your Ethereum account:"
    login_signature = wallet.sign_message(encode_defunct(text=login_message))

    with pytest.raises(InvalidInputError, match="was not made by"):
        await _prove(db_session_factory, account_id, login_signature.signature.to_0x_hex())

    assert await _count_payout_addresses(db_session_factory) == 0


async def test_a_payout_proof_is_not_a_login_signature(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    challenge = await _request(db_session_factory, account_id, wallet)
    issued_at = datetime.now(UTC).replace(microsecond=0)
    login_message = "\n".join(
        [
            "testserver wants you to sign in with your Ethereum account:",
            wallet.address,
            "",
            "URI: http://testserver",
            "Version: 1",
            "Chain ID: 84532",
            f"Nonce: {challenge.nonce}",
            f"Issued At: {issued_at.isoformat().replace('+00:00', 'Z')}",
        ],
    )

    with pytest.raises(ValueError, match="signature is not valid"):
        verify_siwe_signature(
            build_service_settings(),
            message=login_message,
            signature=_sign(challenge, wallet),
            expected_nonce=challenge.nonce,
            now=issued_at,
        )


@pytest.mark.parametrize(
    "forged",
    [
        {"network": "eip155:8453"},
        {"account_id": 999_999},
        {"address": Account.create().address},
        {"nonce": "an-earlier-challenges-nonce"},
    ],
    ids=["another_network", "another_account", "another_address", "another_nonce"],
)
async def test_a_proof_signed_over_other_terms_is_rejected(
    db_session_factory: async_sessionmaker[AsyncSession],
    forged: dict[str, str | int],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    challenge = await _request(db_session_factory, account_id, wallet)
    other_terms = PayoutAddressChallenge(
        **{
            "account_id": challenge.account_id,
            "network": challenge.network,
            "address": challenge.address,
            "nonce": challenge.nonce,
            **forged,
        },
    )

    with pytest.raises(
        InvalidInputError,
        match=f"signature was not made by {wallet.address} over the pending challenge",
    ):
        await _prove(db_session_factory, account_id, _sign(other_terms, wallet))

    assert await _count_payout_addresses(db_session_factory) == 0


async def test_a_proof_is_used_up_with_its_challenge(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    signature = _sign(await _request(db_session_factory, account_id, wallet), wallet)
    await _prove(db_session_factory, account_id, signature)

    with pytest.raises(InvalidStateError, match="no payout address challenge is pending"):
        await _prove(db_session_factory, account_id, signature)

    assert await _count_payout_addresses(db_session_factory) == 1


async def test_a_proof_submitted_twice_at_once_is_recorded_once(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    signature = _sign(await _request(db_session_factory, account_id, wallet), wallet)

    outcomes = await asyncio.gather(
        _prove(db_session_factory, account_id, signature),
        _prove(db_session_factory, account_id, signature),
        return_exceptions=True,
    )

    assert sorted(type(outcome).__name__ for outcome in outcomes) == [
        "InvalidStateError",
        "PayoutAddress",
    ]
    assert await _count_payout_addresses(db_session_factory) == 1


async def test_requesting_a_challenge_again_replaces_the_pending_one(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    first_wallet = Account.create()
    second_wallet = Account.create()
    first = await _request(db_session_factory, account_id, first_wallet)
    second = await _request(db_session_factory, account_id, second_wallet)

    with pytest.raises(InvalidInputError, match="was not made by"):
        await _prove(db_session_factory, account_id, _sign(first, first_wallet))
    proven = await _prove(db_session_factory, account_id, _sign(second, second_wallet))

    assert proven.address == second_wallet.address


async def test_a_challenge_requested_again_in_one_session_carries_the_new_terms(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    settings = build_service_settings()
    first_wallet = Account.create()
    second_wallet = Account.create()
    async with db_session_factory() as session:
        for wallet in (first_wallet, second_wallet):
            challenge = await payout_addresses.request_payout_address_challenge(
                session=session,
                settings=settings,
                account_id=account_id,
                address=wallet.address,
                network=settings.payment_network,
            )

    proven = await _prove(db_session_factory, account_id, _sign(challenge, second_wallet))

    assert proven.address == second_wallet.address


async def test_an_expired_challenge_cannot_be_proven(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    challenge = await _request(db_session_factory, account_id, wallet)
    async with db_session_factory.begin() as session:
        await session.execute(
            update(PayoutAddressChallenge)
            .where(PayoutAddressChallenge.account_id == account_id)
            .values(expires_at=func.now() - timedelta(seconds=1)),
        )

    with pytest.raises(InvalidStateError, match="challenge has expired"):
        await _prove(db_session_factory, account_id, _sign(challenge, wallet))

    assert await _count_payout_addresses(db_session_factory) == 0


async def test_a_challenge_for_another_network_is_refused(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match="network must be eip155:84532"):
            await payout_addresses.request_payout_address_challenge(
                session=session,
                settings=build_service_settings(),
                account_id=account_id,
                address=Account.create().address,
                network="eip155:8453",
            )


async def test_a_lowercase_address_is_challenged_in_its_checksummed_form(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()

    async with db_session_factory() as session:
        challenge = await payout_addresses.request_payout_address_challenge(
            session=session,
            settings=build_service_settings(),
            account_id=account_id,
            address=wallet.address.lower(),
            network=PAYMENT_NETWORK,
        )
    proven = await _prove(db_session_factory, account_id, _sign(challenge, wallet))

    assert (challenge.address, proven.address) == (wallet.address, wallet.address)


@pytest.mark.parametrize(
    ("address", "message"),
    [
        ("0x" + "1" * 41, "invalid EVM address"),
        ("0x036cbD53842c5426634e7929541eC2318f3dCF7e", "invalid EIP-55 checksum"),
    ],
    ids=["overlong", "mistyped_checksum"],
)
async def test_a_malformed_address_is_refused(
    db_session_factory: async_sessionmaker[AsyncSession],
    address: str,
    message: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(InvalidInputError, match=message):
            await payout_addresses.request_payout_address_challenge(
                session=session,
                settings=build_service_settings(),
                account_id=account_id,
                address=address,
                network=PAYMENT_NETWORK,
            )
        assert await session.get(PayoutAddressChallenge, account_id) is None


@pytest.mark.parametrize(
    "signature",
    ["0x" + "00" * 65, "0x" + "11" * 63 + "1b", "0x" + "11" * 65 + "1b", ""],
    ids=["unrecoverable", "too_short", "too_long", "empty"],
)
async def test_a_malformed_signature_is_rejected(
    db_session_factory: async_sessionmaker[AsyncSession],
    signature: str,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    await _request(db_session_factory, account_id, Account.create())

    with pytest.raises(InvalidInputError, match="signature is not valid"):
        await _prove(db_session_factory, account_id, signature)


@pytest.mark.parametrize(
    "reencode",
    [
        # The same signature with s mirrored and v flipped: it recovers the same signer.
        pytest.param(lambda r, s, v: (r, SECPK1_N - s, 55 - v), id="high_s"),
        # An EIP-155 style v, which implies a chain id the domain does not name.
        pytest.param(lambda r, s, v: (r, s, v + 10), id="eip155_v"),
    ],
)
async def test_another_encoding_of_the_right_signature_is_rejected(
    db_session_factory: async_sessionmaker[AsyncSession],
    reencode: Callable[[int, int, int], tuple[int, int, int]],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    signed = _signed(await _request(db_session_factory, account_id, wallet), wallet)

    with pytest.raises(InvalidInputError, match="signature is not valid"):
        await _prove(
            db_session_factory,
            account_id,
            _encode_signature(*reencode(signed.r, signed.s, signed.v)),
        )

    assert await _count_payout_addresses(db_session_factory) == 0


async def test_a_v_of_0_or_1_is_recorded_as_27_or_28(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    signed = _signed(await _request(db_session_factory, account_id, wallet), wallet)

    proven = await _prove(
        db_session_factory,
        account_id,
        _encode_signature(signed.r, signed.s, signed.v - 27),
    )

    assert proven.signature == signed.signature.to_0x_hex()


async def test_a_recorded_proof_cannot_be_updated(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    proven = await _prove_address(db_session_factory, account_id, Account.create())

    with pytest.raises(DBAPIError, match="payout_addresses rows are immutable"):
        async with db_session_factory.begin() as session:
            await session.execute(
                update(PayoutAddress).values(
                    address=Account.create().address,
                    effective_at=proven.verified_at,
                ),
            )

    held = await _effective(db_session_factory, account_id, at=proven.verified_at)
    effective = await _effective(db_session_factory, account_id, at=proven.effective_at)
    assert (held, effective) == (None, proven.address)


async def test_a_recorded_proof_cannot_be_deleted(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    first = await _prove_address(db_session_factory, account_id, Account.create())
    second = await _prove_address(db_session_factory, account_id, Account.create())

    # Deleting the pending proof would hand payouts back to the first address at once.
    with pytest.raises(DBAPIError, match="payout_addresses rows are deleted only with"):
        async with db_session_factory.begin() as session:
            await session.execute(delete(PayoutAddress).where(PayoutAddress.id == second.id))

    held = await _effective(db_session_factory, account_id, at=first.effective_at)
    assert (held, await _count_payout_addresses(db_session_factory)) == (None, 2)


async def test_deleting_an_account_deletes_its_payout_addresses_and_challenge(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    await _prove_address(db_session_factory, account_id, Account.create())
    await _request(db_session_factory, account_id, Account.create())

    async with db_session_factory.begin() as session:
        await session.execute(delete(AccountModel).where(AccountModel.id == account_id))

    async with db_session_factory() as session:
        challenge = await session.get(PayoutAddressChallenge, account_id)
    assert (await _count_payout_addresses(db_session_factory), challenge) == (0, None)


async def test_a_skewed_host_clock_moves_neither_an_expiry_nor_a_hold(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClockAYearAhead(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return super().now(tz) + timedelta(days=365)

    monkeypatch.setattr(payout_addresses, "datetime", ClockAYearAhead)
    settings = build_service_settings()
    account_id = await create_provider_account_record(db_session_factory)
    wallet = Account.create()
    before = await _database_now(db_session_factory)

    challenge = await _request(db_session_factory, account_id, wallet)
    proven = await _prove(db_session_factory, account_id, _sign(challenge, wallet))

    after = await _database_now(db_session_factory)
    expiry = timedelta(seconds=settings.payout_address_challenge_seconds)
    assert before + expiry <= challenge.expires_at <= after + expiry
    assert before <= proven.verified_at <= after
    assert proven.effective_at == proven.verified_at + timedelta(
        seconds=settings.payout_address_hold_seconds,
    )


@pytest.mark.parametrize(
    ("effective_in", "expected"),
    [(timedelta(hours=-1), PAYOUT_ADDRESS), (timedelta(hours=1), None)],
    ids=["hold_ended", "held"],
)
async def test_the_effective_address_is_judged_by_the_database_clock_by_default(
    db_session_factory: async_sessionmaker[AsyncSession],
    effective_in: timedelta,
    expected: str | None,
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    effective_at = await _database_now(db_session_factory) + effective_in
    await _insert_proof(
        db_session_factory,
        account_id,
        verified_at=effective_at - timedelta(days=1),
        effective_at=effective_at,
    )

    assert await _effective(db_session_factory, account_id, at=None) == expected


async def test_no_payout_address_is_effective_before_one_is_proven(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    assert await _effective(db_session_factory, account_id, at=None) is None
    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="no payout address has been proven"):
            await payout_addresses.get_payout_address(
                session=session,
                settings=build_service_settings(),
                account_id=account_id,
            )
