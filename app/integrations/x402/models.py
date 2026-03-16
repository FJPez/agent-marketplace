from __future__ import annotations

from x402 import PaymentRequirements
from x402.schemas import SettleResponse


def to_payment_requirements(payment_requirement: dict[str, object]) -> PaymentRequirements:
    extra = {
        "facilitator_url": payment_requirement["facilitator_url"],
        "network": payment_requirement["network"],
        "currency": payment_requirement["currency"],
        "amount_minor": payment_requirement["amount_minor"],
    }
    if "name" in payment_requirement:
        extra["name"] = payment_requirement["name"]
    if "version" in payment_requirement:
        extra["version"] = payment_requirement["version"]

    return PaymentRequirements.model_validate(
        {
            "scheme": str(payment_requirement["scheme"]),
            "network": str(payment_requirement["network_caip2"]),
            "asset": str(payment_requirement["asset"]),
            "amount": str(payment_requirement["amount_minor"]),
            "payTo": str(payment_requirement["pay_to"]),
            "maxTimeoutSeconds": _int_value(payment_requirement["max_timeout_seconds"]),
            "extra": extra,
        }
    )


def to_settle_response(settle_outcome: dict[str, object]) -> SettleResponse:
    return SettleResponse.model_validate(settle_outcome)


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    msg = "payment requirement contains a non-integer timeout"
    raise TypeError(msg)
