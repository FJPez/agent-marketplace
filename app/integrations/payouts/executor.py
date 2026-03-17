from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from eth_account import Account
from web3 import HTTPProvider, Web3

ERC20_TRANSFER_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


class PayoutExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedPayout:
    raw_transaction: str
    reference: str
    network: str
    token_address: str


@dataclass(frozen=True, slots=True)
class SentPayout:
    reference: str
    network: str
    token_address: str


@runtime_checkable
class SupportsPayoutExecutor(Protocol):
    async def current_nonce(self) -> int: ...

    async def prepare_payout(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
        idempotency_key: str,
        nonce: int,
    ) -> PreparedPayout: ...

    async def send_prepared_payout(
        self,
        *,
        raw_transaction: str,
        reference: str,
    ) -> SentPayout: ...


@dataclass(slots=True, repr=False)
class BaseSepoliaUsdcPayoutExecutor:
    rpc_url: str
    chain_id: int
    token_address: str
    private_key: str = field(repr=False)

    async def current_nonce(self) -> int:
        return await asyncio.to_thread(self._current_nonce_sync)

    async def prepare_payout(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
        idempotency_key: str,
        nonce: int,
    ) -> PreparedPayout:
        if amount_minor <= 0:
            msg = "payout amount must be positive"
            raise PayoutExecutionError(msg)
        if not Web3.is_address(destination_wallet):
            msg = "invalid destination wallet address"
            raise PayoutExecutionError(msg)
        _ = idempotency_key
        return await asyncio.to_thread(
            self._prepare_payout_sync,
            destination_wallet=destination_wallet,
            amount_minor=amount_minor,
            nonce=nonce,
        )

    async def send_prepared_payout(
        self,
        *,
        raw_transaction: str,
        reference: str,
    ) -> SentPayout:
        return await asyncio.to_thread(
            self._send_prepared_payout_sync,
            raw_transaction=raw_transaction,
            reference=reference,
        )

    def _current_nonce_sync(self) -> int:
        web3 = Web3(HTTPProvider(self.rpc_url))
        sender = Account.from_key(self.private_key)
        sender_address = Web3.to_checksum_address(sender.address)
        return int(web3.eth.get_transaction_count(sender_address, block_identifier="pending"))

    def _prepare_payout_sync(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
        nonce: int,
    ) -> PreparedPayout:
        web3 = Web3(HTTPProvider(self.rpc_url))
        sender = Account.from_key(self.private_key)
        sender_address = Web3.to_checksum_address(sender.address)
        recipient = Web3.to_checksum_address(destination_wallet)
        token_address = Web3.to_checksum_address(self.token_address)
        contract = web3.eth.contract(address=token_address, abi=ERC20_TRANSFER_ABI)
        gas_price = web3.eth.gas_price
        transaction = contract.functions.transfer(
            recipient,
            amount_minor,
        ).build_transaction(
            {
                "chainId": self.chain_id,
                "from": sender_address,
                "nonce": nonce,
                "gasPrice": gas_price,
            }
        )
        signed = web3.eth.account.sign_transaction(transaction, private_key=self.private_key)
        return PreparedPayout(
            raw_transaction=signed.raw_transaction.hex(),
            reference=signed.hash.hex(),
            network="base-sepolia",
            token_address=token_address,
        )

    def _send_prepared_payout_sync(
        self,
        *,
        raw_transaction: str,
        reference: str,
    ) -> SentPayout:
        web3 = Web3(HTTPProvider(self.rpc_url))
        try:
            tx_hash = web3.eth.send_raw_transaction(_decode_hex_bytes(raw_transaction))
        except ValueError as exc:
            message = str(exc).lower()
            if "already known" not in message and "already imported" not in message:
                raise PayoutExecutionError(str(exc)) from exc
            tx_hash = _decode_hex_bytes(reference)
        return SentPayout(
            reference=tx_hash.hex(),
            network="base-sepolia",
            token_address=Web3.to_checksum_address(self.token_address),
        )


def _decode_hex_bytes(value: str) -> bytes:
    normalized = value.removeprefix("0x")
    return bytes.fromhex(normalized)
