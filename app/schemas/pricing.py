from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.schemas.common import Id, Timestamp


class ListingPriceRequest(BaseModel):
    """A provider's price for a paid endpoint.

    `amount` is in atomic units of the marketplace's payment asset (for USDC,
    1,000,000 is 1 USDC). The marketplace adds the asset, network, pay_to,
    validity window and fee from its settings when it stores the price version.
    """

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
