import pytest

from app.core.evm import checksum_address

# An address in its EIP-55 form, and the same address with one letter's case flipped.
CHECKSUMMED_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
MISTYPED_ADDRESS = "0x036cbD53842c5426634e7929541eC2318f3dCF7e"


@pytest.mark.parametrize(
    "address",
    [
        pytest.param(CHECKSUMMED_ADDRESS.lower(), id="lowercase"),
        pytest.param("0x" + CHECKSUMMED_ADDRESS[2:].upper(), id="uppercase"),
        pytest.param(CHECKSUMMED_ADDRESS, id="checksummed"),
    ],
)
def test_checksum_address_returns_the_eip55_form(address: str) -> None:
    assert checksum_address(address) == CHECKSUMMED_ADDRESS


@pytest.mark.parametrize(
    ("address", "message"),
    [
        pytest.param(
            MISTYPED_ADDRESS,
            "address has an invalid EIP-55 checksum",
            id="mistyped_checksum",
        ),
        pytest.param("0x1234", "invalid EVM address", id="not_an_address"),
        pytest.param(CHECKSUMMED_ADDRESS[2:], "invalid EVM address", id="missing_0x_prefix"),
        pytest.param(
            "0X" + CHECKSUMMED_ADDRESS[2:],
            "invalid EVM address",
            id="uppercase_0x_prefix",
        ),
        pytest.param(" " + CHECKSUMMED_ADDRESS, "invalid EVM address", id="leading_space"),
        pytest.param(CHECKSUMMED_ADDRESS + " ", "invalid EVM address", id="trailing_space"),
        pytest.param(CHECKSUMMED_ADDRESS[:-1], "invalid EVM address", id="too_short"),
        pytest.param(CHECKSUMMED_ADDRESS + "e", "invalid EVM address", id="too_long"),
        pytest.param(
            CHECKSUMMED_ADDRESS[:-1] + "g",
            "invalid EVM address",
            id="non_hex_character",
        ),
    ],
)
def test_checksum_address_rejects_what_is_not_a_well_typed_address(
    address: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        checksum_address(address)
