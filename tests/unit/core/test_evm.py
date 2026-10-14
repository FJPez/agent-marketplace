from eth_account import Account

from app.core.evm import normalize_wallet_address


def test_normalize_wallet_address_returns_checksum_address() -> None:
    signer = Account.create()

    normalized = normalize_wallet_address(signer.address.lower())

    assert normalized == signer.address
