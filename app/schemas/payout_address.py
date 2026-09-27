from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.core.json_types import JsonObject
from app.core.security import EVM_SIGNATURE_PATTERN
from app.db.models import PayoutAddress
from app.schemas.common import Timestamp, WalletAddress


class PayoutAddressChallengeRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "address": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                    "network": "eip155:84532",
                }
            ]
        },
    )

    address: WalletAddress
    # A CAIP-2 chain id; it must be the marketplace's payment network.
    network: str


class PayoutAddressChallengeResponse(BaseModel):
    """Sign `typed_data` with the address's key (eth_signTypedData_v4) before `expires_at`."""

    nonce: str
    expires_at: Timestamp
    typed_data: JsonObject


class PayoutAddressProofRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"signature": "0x" + "ab" * 65}]},
    )

    signature: Annotated[str, StringConstraints(pattern=EVM_SIGNATURE_PATTERN)]


class PayoutAddressResponse(BaseModel):
    """A provider's latest proven payout address.

    Payouts are held while it is pending, until `effective_at`; then they go to it.
    """

    address: str
    network: str
    verified_at: Timestamp
    effective_at: Timestamp
    status: Literal["pending", "effective"]

    @classmethod
    def from_model(cls, payout_address: PayoutAddress, *, now: datetime) -> Self:
        return cls(
            address=payout_address.address,
            network=payout_address.network,
            verified_at=payout_address.verified_at,
            effective_at=payout_address.effective_at,
            status="effective" if payout_address.effective_at <= now else "pending",
        )
