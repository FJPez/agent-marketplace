"""EVM addresses: how they are checked, and the Pydantic types that check them."""

import re
from typing import Annotated, Final

from eth_utils import is_checksum_address, is_checksum_formatted_address, to_checksum_address
from pydantic import AfterValidator

ADDRESS_SHAPE: Final[re.Pattern[str]] = re.compile(r"0x[0-9a-fA-F]{40}")
ZERO_ADDRESS: Final[str] = "0x0000000000000000000000000000000000000000"


def checksum_address(value: str) -> str:
    """Return the EIP-55 form of an EVM address.

    The value must be a lowercase `0x` followed by exactly 40 hex digits: no
    surrounding whitespace, no missing or uppercase prefix, no wrong length. An
    all-lowercase or all-uppercase address carries no checksum and is accepted. A
    mixed-case one must already be correctly checksummed: a wrong checksum means a
    mistyped address, which would sign in, or be paid, as someone else.
    """
    if not ADDRESS_SHAPE.fullmatch(value):
        msg = "invalid EVM address"
        raise ValueError(msg)
    if is_checksum_formatted_address(value) and not is_checksum_address(value):
        msg = "address has an invalid EIP-55 checksum"
        raise ValueError(msg)
    return to_checksum_address(value)


def _reject_zero_address(value: str) -> str:
    # A common placeholder: payments to it would fail at settlement, not at startup.
    if value == ZERO_ADDRESS:
        msg = "address must not be the zero address"
        raise ValueError(msg)
    return value


WalletAddress = Annotated[str, AfterValidator(checksum_address)]
# An address that payments use: the treasury, the asset contract.
PaymentAddress = Annotated[
    str,
    AfterValidator(checksum_address),
    AfterValidator(_reject_zero_address),
]
