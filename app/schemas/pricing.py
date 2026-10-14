from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.schemas.common import Id, Timestamp


class ListingPriceRequest(BaseModel):
    """A paid endpoint's price, in atomic units of the payment asset (1 USDC = 1,000,000)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"amount": 10_000}]},
    )

    # The amount of an EIP-3009 payment authorization is a uint256.
    amount: Annotated[StrictInt, Field(gt=0, le=2**256 - 1)]


class ListingPriceResponse(BaseModel):
    """One immutable price version, as its provider sees it."""

    model_config = ConfigDict(from_attributes=True)

    id: Id
    version: int
    amount: int
    asset: str
    network: str
    pay_to: str
    max_timeout_seconds: int
    fee_bps: int
    created_at: Timestamp
