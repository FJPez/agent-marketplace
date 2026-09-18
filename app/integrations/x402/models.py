"""Typed x402 values exchanged between the service layer and the protocol SDK."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from x402 import PaymentRequirements
from x402.http import decode_payment_signature_header
from x402.schemas import SettleResponse, VerifyResponse


class InvalidPaymentPayloadError(Exception):
    pass


class PaymentRequirement(BaseModel):
    """What the marketplace demands for one paid invoke, in marketplace terms."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scheme: str
    asset: str
    amount_minor: int
    payment_amount: int
    currency: str
    pay_to: str
    network: str
    network_caip2: str
    facilitator_url: str
    max_timeout_seconds: int
    name: str
    version: str

    def to_sdk(self) -> PaymentRequirements:
        return PaymentRequirements.model_validate(
            {
                "scheme": self.scheme,
                "network": self.network_caip2,
                "asset": self.asset,
                "amount": str(self.payment_amount),
                "payTo": self.pay_to,
                "maxTimeoutSeconds": self.max_timeout_seconds,
                "extra": {
                    "facilitator_url": self.facilitator_url,
                    "network": self.network,
                    "currency": self.currency,
                    "amount_minor": self.amount_minor,
                    "name": self.name,
                    "version": self.version,
                },
            }
        )


class PaymentPayload(BaseModel):
    """A payer's x402 v2 payload, with the two fields the marketplace decides on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identifier: str
    accepted_asset: str
    wire: dict[str, object]

    @classmethod
    def from_header(cls, header_value: str) -> PaymentPayload:
        try:
            decoded = decode_payment_signature_header(header_value)
        except (ValueError, TypeError) as exc:
            raise InvalidPaymentPayloadError(
                "payment payload is not a valid x402 v2 payload"
            ) from exc
        if decoded.x402_version != 2:
            raise InvalidPaymentPayloadError("payment payload is not a valid x402 v2 payload")
        return cls.from_stored(decoded.model_dump(by_alias=True, exclude_none=True))

    @classmethod
    def from_stored(cls, wire: dict[str, object]) -> PaymentPayload:
        payload = _nested_object(wire, "payload")
        authorization = _nested_object(payload, "authorization")
        identifier = _first_non_empty_string(
            authorization.get("nonce"),
            payload.get("transaction"),
            payload.get("signature"),
        )
        if identifier is None:
            raise InvalidPaymentPayloadError("payment identifier is missing")
        accepted_asset = _nested_object(wire, "accepted").get("asset")
        if not isinstance(accepted_asset, str):
            raise InvalidPaymentPayloadError("payment payload does not name an accepted asset")
        return cls(identifier=identifier, accepted_asset=accepted_asset, wire=wire)

    def matches(self, requirement: PaymentRequirement) -> bool:
        return self.accepted_asset.casefold() == requirement.asset.casefold()


class VerifyOutcome(BaseModel):
    """The answer to "may this payment proceed", from whoever decided it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    reason: str | None = None
    message: str | None = None
    payer: str | None = None
    checked_by: Literal["facilitator", "resource_server"]

    @classmethod
    def from_sdk(cls, response: VerifyResponse) -> VerifyOutcome:
        return cls(
            accepted=response.is_valid,
            reason=response.invalid_reason,
            message=response.invalid_message,
            payer=response.payer,
            checked_by="facilitator",
        )

    @classmethod
    def asset_mismatch(cls) -> VerifyOutcome:
        return cls(
            accepted=False,
            reason="asset_mismatch",
            message="payment asset does not match the requirement",
            checked_by="resource_server",
        )


class SettleOutcome(BaseModel):
    """The result of asking the facilitator to move the funds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    transaction: str | None = None
    network: str | None = None
    payer: str | None = None
    amount: str | None = None
    error_reason: str | None = None
    error_message: str | None = None

    @property
    def reference(self) -> str | None:
        return self.transaction

    @classmethod
    def from_sdk(cls, response: SettleResponse) -> SettleOutcome:
        return cls(
            success=response.success,
            transaction=response.transaction,
            network=response.network,
            payer=response.payer,
            amount=response.amount,
            error_reason=response.error_reason,
            error_message=response.error_message,
        )

    def to_sdk(self) -> SettleResponse:
        if self.transaction is None or self.network is None:
            msg = "a settle outcome without a transaction and a network cannot be encoded"
            raise ValueError(msg)
        return SettleResponse.model_validate(
            {
                "success": self.success,
                "transaction": self.transaction,
                "network": self.network,
                "payer": self.payer,
                "amount": self.amount,
                "errorReason": self.error_reason,
                "errorMessage": self.error_message,
            }
        )


def _nested_object(source: dict[str, object], key: str) -> dict[str, object]:
    value = source.get(key)
    if isinstance(value, dict):
        return {str(name): item for name, item in value.items()}
    return {}


def _first_non_empty_string(*candidates: object) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None
