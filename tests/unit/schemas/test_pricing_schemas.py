import pytest
from pydantic import ValidationError

from app.schemas.pricing import ListingPriceRequest


@pytest.mark.parametrize("amount", [1, 10_000, 2**256 - 1], ids=["one", "usual", "max_uint256"])
def test_listing_price_request_accepts_positive_uint256_amounts(amount: int) -> None:
    assert ListingPriceRequest.model_validate({"amount": amount}).amount == amount


@pytest.mark.parametrize(
    "payload",
    [
        {"amount": 0},
        {"amount": -1},
        {"amount": 2**256},
        {"amount": True},
        {"amount": "10000"},
        {"amount": 10000.0},
        {"amount": 10_000, "currency": "USD"},
        {},
    ],
    ids=["zero", "negative", "above_uint256", "bool", "string", "float", "extra_field", "missing"],
)
def test_listing_price_request_rejects_invalid_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ListingPriceRequest.model_validate(payload)
