import pytest

from app.integrations.x402.payment_requirements import (
    PaymentRequirementConfigError,
    build_payment_requirement,
)


def test_build_payment_requirement_requires_pay_to_address() -> None:
    with pytest.raises(
        PaymentRequirementConfigError,
        match="APP_X402_PAY_TO_ADDRESS is required for paid invokes",
    ):
        build_payment_requirement(
            amount_minor=250,
            currency="USD",
            pay_to_address=None,
            facilitator_url="https://x402.org/facilitator",
            network="base-sepolia",
            network_caip2="eip155:84532",
        )
