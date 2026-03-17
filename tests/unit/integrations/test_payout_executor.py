from __future__ import annotations

from unittest.mock import patch

import pytest

from app.integrations.payouts.executor import BaseSepoliaUsdcPayoutExecutor, PayoutExecutionError


def _executor() -> BaseSepoliaUsdcPayoutExecutor:
    return BaseSepoliaUsdcPayoutExecutor(
        rpc_url="http://localhost:8545",
        chain_id=84532,
        token_address="0x" + "ab" * 20,
        private_key="0x" + "cd" * 32,
    )


@pytest.mark.asyncio
async def test_prepare_payout_rejects_non_positive_amount() -> None:
    with pytest.raises(PayoutExecutionError, match="payout amount must be positive"):
        await _executor().prepare_payout(
            destination_wallet="0x" + "aa" * 20,
            amount_minor=0,
            idempotency_key="payout-request-1",
            nonce=1,
        )


@pytest.mark.asyncio
async def test_prepare_payout_rejects_invalid_wallet_address() -> None:
    with pytest.raises(PayoutExecutionError, match="invalid destination wallet address"):
        await _executor().prepare_payout(
            destination_wallet="not-an-address",
            amount_minor=100,
            idempotency_key="payout-request-1",
            nonce=1,
        )


@pytest.mark.asyncio
async def test_prepare_payout_passes_nonce_to_sync_helper() -> None:
    executor = _executor()

    with patch.object(
        BaseSepoliaUsdcPayoutExecutor,
        "_prepare_payout_sync",
        autospec=True,
        return_value={"raw_transaction": "0xraw", "reference": "0xref"},
    ) as prepare_sync:
        result = await executor.prepare_payout(
            destination_wallet="0x" + "aa" * 20,
            amount_minor=100,
            idempotency_key="payout-request-1",
            nonce=7,
        )

    assert result == {"raw_transaction": "0xraw", "reference": "0xref"}
    prepare_sync.assert_called_once_with(
        executor,
        destination_wallet="0x" + "aa" * 20,
        amount_minor=100,
        nonce=7,
    )


@pytest.mark.asyncio
async def test_send_prepared_payout_passes_reference_to_sync_helper() -> None:
    executor = _executor()

    with patch.object(
        BaseSepoliaUsdcPayoutExecutor,
        "_send_prepared_payout_sync",
        autospec=True,
        return_value={"reference": "0xref"},
    ) as send_sync:
        result = await executor.send_prepared_payout(
            raw_transaction="0xraw",
            reference="0xref",
        )

    assert result == {"reference": "0xref"}
    send_sync.assert_called_once_with(
        executor,
        raw_transaction="0xraw",
        reference="0xref",
    )


def test_executor_repr_does_not_expose_private_key() -> None:
    rendered = repr(_executor())

    assert "cdcd" not in rendered
