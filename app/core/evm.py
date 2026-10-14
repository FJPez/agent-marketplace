"""EVM addresses: how they are checked, and the Pydantic types that check them."""

from typing import Annotated, Final

from eth_utils import (
    is_address,
    is_checksum_address,
    is_checksum_formatted_address,
    to_checksum_address,
)
from pydantic import AfterValidator

ZERO_ADDRESS: Final[str] = "0x0000000000000000000000000000000000000000"


def normalize_wallet_address(wallet_address: str) -> str:
    if not is_address(wallet_address):
        msg = "invalid wallet address"
        raise ValueError(msg)
    return to_checksum_address(wallet_address)


def _checksum_address(value: str) -> str:
    """Return the EIP-55 form; a mixed-case address must already carry a valid checksum."""
    if is_checksum_formatted_address(value) and not is_checksum_address(value):
        msg = "address has an invalid EIP-55 checksum"
        raise ValueError(msg)
    return normalize_wallet_address(value)


def _reject_zero_address(value: str) -> str:
    # A common placeholder: payments to it would fail at settlement, not at startup.
    if value == ZERO_ADDRESS:
        msg = "address must not be the zero address"
        raise ValueError(msg)
    return value


WalletAddress = Annotated[str, AfterValidator(normalize_wallet_address)]
# An address that payments use: the treasury, the asset contract.
PaymentAddress = Annotated[
    str,
    AfterValidator(_checksum_address),
    AfterValidator(_reject_zero_address),
]
