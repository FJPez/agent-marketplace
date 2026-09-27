"""Provider payout addresses: proven with an EIP-712 signature, then held before use.

A provider asks for a challenge naming the address and network, signs the challenge's
typed data with that address's key (`eth_signTypedData_v4`) and submits the signature.
The typed data binds the proof to the provider's account, the address, the network and
its purpose, under a server-issued nonce that expires and can be used once, so neither
a login signature (EIP-191 text) nor a proof made for another account, address or
network can pass for it. Every proof is kept, and the latest one on a network decides
where payouts go, once its hold has ended.
"""

import re
from datetime import UTC, datetime, timedelta

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_keys.exceptions import BadSignature
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidInputError, InvalidStateError, NotFoundError
from app.core.json_types import JsonObject
from app.core.security import EVM_SIGNATURE_PATTERN, checksum_address, generate_nonce
from app.db.models import PayoutAddress, PayoutAddressChallenge


async def request_payout_address_challenge(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
    address: str,
    network: str,
) -> PayoutAddressChallenge:
    """Issue the challenge that proves `address` on `network`, replacing a pending one.

    The address is challenged, and later recorded, in its EIP-55 form.
    """
    if network != settings.payment_network:
        raise InvalidInputError(
            f"network must be {settings.payment_network}, the network payouts are sent on",
        )
    try:
        checksummed = checksum_address(address)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    values = {
        "network": network,
        "address": checksummed,
        "nonce": generate_nonce(),
        "expires_at": datetime.now(UTC)
        + timedelta(seconds=settings.payout_address_challenge_seconds),
    }
    challenge = (
        await session.execute(
            insert(PayoutAddressChallenge)
            .values(account_id=account_id, **values)
            .on_conflict_do_update(index_elements=[PayoutAddressChallenge.account_id], set_=values)
            .returning(PayoutAddressChallenge)
            # A challenge this session loaded earlier would otherwise be returned as it
            # was, not as replaced.
            .execution_options(populate_existing=True),
        )
    ).scalar_one()
    await session.commit()
    return challenge


def proof_typed_data(challenge: PayoutAddressChallenge) -> JsonObject:
    """The EIP-712 typed data whose signature by the challenge's address proves it."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "PayoutAddressProof": [
                {"name": "accountId", "type": "uint256"},
                {"name": "payoutAddress", "type": "address"},
                {"name": "network", "type": "string"},
                {"name": "nonce", "type": "string"},
            ],
        },
        "primaryType": "PayoutAddressProof",
        "domain": {
            "name": "Agent Marketplace",
            "version": "1",
            "chainId": int(challenge.network.removeprefix("eip155:")),
        },
        "message": {
            "accountId": challenge.account_id,
            "payoutAddress": challenge.address,
            "network": challenge.network,
            "nonce": challenge.nonce,
        },
    }


async def prove_payout_address(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
    signature: str,
) -> PayoutAddress:
    """Record the pending challenge's address as the provider's payout address.

    It takes over at once from any earlier one, and payouts are held until it becomes
    effective. The challenge is consumed, so the same proof cannot be recorded twice.
    """
    if not re.fullmatch(EVM_SIGNATURE_PATTERN, signature):
        raise InvalidInputError("signature is not valid")
    challenge = await session.scalar(
        select(PayoutAddressChallenge)
        .where(PayoutAddressChallenge.account_id == account_id)
        .with_for_update(),
    )
    if challenge is None:
        raise InvalidStateError("no payout address challenge is pending; request one first")
    now = datetime.now(UTC)
    if challenge.expires_at <= now:
        raise InvalidStateError("the payout address challenge has expired; request a new one")
    # A well-formed signature can still carry an invalid v (ValueError) or an r or s
    # that recovers no key (BadSignature).
    try:
        signer = Account.recover_message(
            encode_typed_data(full_message=proof_typed_data(challenge)),
            signature=signature,
        )
    except (BadSignature, ValueError) as exc:
        raise InvalidInputError("signature is not valid") from exc
    if signer != challenge.address:
        raise InvalidInputError(
            f"signature was not made by {challenge.address} over the pending challenge",
        )

    payout_address = PayoutAddress(
        account_id=account_id,
        network=challenge.network,
        address=challenge.address,
        nonce=challenge.nonce,
        signature=signature,
        verified_at=now,
        effective_at=now + timedelta(seconds=settings.payout_address_hold_seconds),
    )
    session.add(payout_address)
    await session.delete(challenge)
    await session.commit()
    return payout_address


async def get_payout_address(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
) -> PayoutAddress:
    """The provider's latest payout address on the payment network, held or not."""
    latest = await _latest_payout_address(
        session=session,
        account_id=account_id,
        network=settings.payment_network,
    )
    if latest is None:
        raise NotFoundError(f"no payout address has been proven on {settings.payment_network}")
    return latest


async def effective_payout_address(
    *,
    session: AsyncSession,
    account_id: int,
    network: str,
    at: datetime,
) -> str | None:
    """The address payouts to the provider on `network` may be sent to at `at`.

    None while payouts are held: before the provider's latest proof becomes effective,
    even when an earlier address was effective (a change holds payouts), and when the
    provider has proven none. For the payout builder.
    """
    latest = await _latest_payout_address(session=session, account_id=account_id, network=network)
    if latest is None or latest.effective_at > at:
        return None
    return latest.address


async def _latest_payout_address(
    *,
    session: AsyncSession,
    account_id: int,
    network: str,
) -> PayoutAddress | None:
    return await session.scalar(
        select(PayoutAddress)
        .where(PayoutAddress.account_id == account_id, PayoutAddress.network == network)
        .order_by(PayoutAddress.id.desc())
        .limit(1),
    )
