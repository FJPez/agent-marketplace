import pytest
from tests.helpers.x402 import build_payment_payload, build_payment_requirement

from app.integrations.x402.models import PaymentPayload, PaymentRequirement


@pytest.fixture
def payment_requirement() -> PaymentRequirement:
    return build_payment_requirement()


@pytest.fixture
def payment_payload() -> PaymentPayload:
    return build_payment_payload()
