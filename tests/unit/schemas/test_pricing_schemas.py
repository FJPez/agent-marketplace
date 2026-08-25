import pytest
from pydantic import ValidationError

from app.schemas.pricing import FixedPrice


@pytest.mark.parametrize(
    "payload",
    [
        {"amount_minor": 0, "currency": "USD"},
        {"amount_minor": True, "currency": "USD"},
        {"amount_minor": "100", "currency": "USD"},
        {"amount_minor": 100, "currency": "usd"},
        {"amount_minor": 100, "currency": "US"},
        {"amount_minor": 100, "currency": "USD", "pricing_type": "fixed_per_call"},
        {"amount_minor": 100},
    ],
)
def test_fixed_price_rejects_invalid_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FixedPrice.model_validate(payload)


def test_fixed_price_normalizes_currency() -> None:
    price = FixedPrice(amount_minor=100, currency=" USD ")

    assert price.amount_minor == 100
    assert price.currency == "USD"


def test_fixed_price_is_frozen() -> None:
    price = FixedPrice(amount_minor=100, currency="USD")

    with pytest.raises(ValidationError):
        price.amount_minor = 200
