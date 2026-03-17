import pytest

from app.core.config import PaymentToken
from app.integrations.x402.payment_requirements import (
    PaymentRequirementConfigError,
    build_payment_requirement,
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
            payment_token=PaymentToken(
                address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                name="USDC",
                symbol="USDC",
                decimals=6,
                version="2",
            ),
            facilitator_url="https://x402.org/facilitator",
            network="base-sepolia",
            network_caip2="eip155:84532",
        )
