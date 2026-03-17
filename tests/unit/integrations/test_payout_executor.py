from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from app.integrations.payouts.executor import BaseSepoliaUsdcPayoutExecutor, PayoutExecutionError


@pytest.mark.asyncio
async def test_send_payout_serializes_concurrent_calls() -> None:
    executor = BaseSepoliaUsdcPayoutExecutor(
        rpc_url="http://localhost:8545",
        chain_id=84532,
        token_address="0x" + "ab" * 20,
        private_key="0x" + "cd" * 32,
    )

    call_order: list[str] = []

    def slow_sync(
        self: BaseSepoliaUsdcPayoutExecutor,
        *,
        destination_wallet: str,
        amount_minor: int,
    ) -> dict[str, object]:
        _ = self
        _ = destination_wallet
        _ = amount_minor
        call_order.append("start")
        time.sleep(0.05)
        call_order.append("end")
        return {"reference": "0x" + "ef" * 32}

    with patch.object(
        BaseSepoliaUsdcPayoutExecutor,
        "_send_payout_sync",
        autospec=True,
        side_effect=slow_sync,
    ):
        await asyncio.gather(
            *(
                executor.send_payout(
                    destination_wallet="0x" + "aa" * 20,
                    amount_minor=100,
                    idempotency_key=f"payment-attempt:{index}",
                )
                for index in range(3)
            )
        )

    assert call_order == ["start", "end", "start", "end", "start", "end"]


@pytest.mark.asyncio
async def test_send_payout_rejects_invalid_wallet_address() -> None:
    executor = BaseSepoliaUsdcPayoutExecutor(
        rpc_url="http://localhost:8545",
        chain_id=84532,
        token_address="0x" + "ab" * 20,
        private_key="0x" + "cd" * 32,
    )

    with pytest.raises(PayoutExecutionError, match="invalid destination wallet address"):
        await executor.send_payout(
            destination_wallet="not-an-address",
            amount_minor=100,
            idempotency_key="payment-attempt:1",
        )
