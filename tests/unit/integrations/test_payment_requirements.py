import pytest

from app.integrations.x402.payment_requirements import (
    PaymentRequirementConfigError,
    build_payment_requirement,
)


def test_build_payment_requirement_for_usd_fixed_price() -> None:
    requirement = build_payment_requirement(
        amount_minor=500,
        currency="USD",
        pay_to_address="0x000000000000000000000000000000000000c0de",
        facilitator_url="https://x402.org/facilitator",
        network="base-sepolia",
        network_caip2="eip155:84532",
    )

    assert requirement["asset"] == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    assert requirement["amount_minor"] == 500
    assert requirement["currency"] == "USD"
    assert requirement["pay_to"] == "0x000000000000000000000000000000000000c0de"
    assert requirement["network"] == "base-sepolia"
    assert requirement["network_caip2"] == "eip155:84532"
    assert requirement["facilitator_url"] == "https://x402.org/facilitator"
    assert requirement["name"] == "USDC"
    assert requirement["version"] == "2"


def test_build_payment_requirement_rejects_non_usd_currency() -> None:
    with pytest.raises(PaymentRequirementConfigError, match="payment currency is not supported"):
        build_payment_requirement(
            amount_minor=500,
            currency="EUR",
            pay_to_address="0x000000000000000000000000000000000000c0de",
            facilitator_url="https://x402.org/facilitator",
            network="base-sepolia",
            network_caip2="eip155:84532",
        )
