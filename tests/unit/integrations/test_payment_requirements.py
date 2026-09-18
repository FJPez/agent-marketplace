import pytest

from app.core.config import PaymentToken
from app.integrations.x402.payment_requirements import (
    PaymentRequirementConfigError,
    build_payment_requirement,
)

USDC = PaymentToken(
    address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    name="USDC",
    symbol="USDC",
    decimals=6,
    version="2",
)


def test_build_payment_requirement_for_usd_fixed_price() -> None:
    requirement = build_payment_requirement(
        amount_minor=500,
        currency="USD",
        treasury_address="0x000000000000000000000000000000000000c0de",
        payment_token=USDC,
        facilitator_url="https://x402.org/facilitator",
        network="base-sepolia",
        network_caip2="eip155:84532",
    )

    assert requirement.scheme == "exact"
    assert requirement.asset == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    assert requirement.amount_minor == 500
    assert requirement.payment_amount == 5_000_000
    assert requirement.currency == "USD"
    assert requirement.pay_to == "0x000000000000000000000000000000000000c0de"
    assert requirement.network == "base-sepolia"
    assert requirement.network_caip2 == "eip155:84532"
    assert requirement.facilitator_url == "https://x402.org/facilitator"
    assert requirement.max_timeout_seconds == 300
    assert requirement.name == "USDC"
    assert requirement.version == "2"


def test_build_payment_requirement_rejects_non_usd_currency() -> None:
    with pytest.raises(PaymentRequirementConfigError, match="payment currency is not supported"):
        build_payment_requirement(
            amount_minor=500,
            currency="EUR",
            treasury_address="0x000000000000000000000000000000000000c0de",
            payment_token=USDC,
            facilitator_url="https://x402.org/facilitator",
            network="base-sepolia",
            network_caip2="eip155:84532",
        )


def test_build_payment_requirement_requires_treasury_address() -> None:
    with pytest.raises(
        PaymentRequirementConfigError,
        match="APP_TREASURY_PRIVATE_KEY is required for paid invokes",
    ):
        build_payment_requirement(
            amount_minor=250,
            currency="USD",
            treasury_address=None,
            payment_token=USDC,
            facilitator_url="https://x402.org/facilitator",
            network="base-sepolia",
            network_caip2="eip155:84532",
        )
