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


@runtime_checkable
class SupportsPayoutExecutor(Protocol):
    async def send_payout(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
        idempotency_key: str,
    ) -> dict[str, object]: ...


@dataclass(slots=True)
class BaseSepoliaUsdcPayoutExecutor:
    rpc_url: str
    chain_id: int
    token_address: str
    private_key: str
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def send_payout(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        if amount_minor <= 0:
            msg = "payout amount must be positive"
            raise PayoutExecutionError(msg)
        if not Web3.is_address(destination_wallet):
            msg = "invalid destination wallet address"
            raise PayoutExecutionError(msg)
        # Base Sepolia ERC-20 transfers rely on nonce sequencing rather than
        # application-level idempotency. The interface keeps this value for
        # future executor implementations that support explicit request keys.
        _ = idempotency_key
        async with self._lock:
            return await asyncio.to_thread(
                self._send_payout_sync,
                destination_wallet=destination_wallet,
                amount_minor=amount_minor,
            )

    def _send_payout_sync(
        self,
        *,
        destination_wallet: str,
        amount_minor: int,
    ) -> dict[str, object]:
        web3 = Web3(HTTPProvider(self.rpc_url))
        sender = Account.from_key(self.private_key)
        sender_address = Web3.to_checksum_address(sender.address)
        recipient = Web3.to_checksum_address(destination_wallet)
        token_address = Web3.to_checksum_address(self.token_address)
        contract = web3.eth.contract(address=token_address, abi=ERC20_TRANSFER_ABI)
        nonce = web3.eth.get_transaction_count(sender_address, block_identifier="pending")
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
        tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
        return {
            "reference": tx_hash.hex(),
            "network": "base-sepolia",
            "token_address": token_address,
        }
