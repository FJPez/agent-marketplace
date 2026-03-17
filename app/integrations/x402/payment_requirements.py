from app.core.config import PaymentToken


class PaymentRequirementConfigError(Exception):
    pass


_USD_MINOR_UNIT_EXPONENT = 2


def build_payment_requirement(
    *,
    amount_minor: int,
    currency: str | None,
    treasury_address: str | None,
    payment_token: PaymentToken | None,
    facilitator_url: str,
    network: str,
    network_caip2: str,
) -> dict[str, object]:
    if treasury_address is None:
        raise PaymentRequirementConfigError(
            "payment configuration is incomplete: "
            "APP_TREASURY_PRIVATE_KEY is required for paid invokes",
        )
    if payment_token is None:
        raise PaymentRequirementConfigError("payment network is not supported")
    if currency != "USD":
        raise PaymentRequirementConfigError("payment currency is not supported")

    return {
        "scheme": "exact",
        "asset": payment_token.address,
        "amount_minor": amount_minor,
        "payment_amount": _to_payment_amount(
            amount_minor=amount_minor,
            asset_decimals=payment_token.decimals,
        ),
        "currency": currency,
        "pay_to": treasury_address,
        "network": network,
        "network_caip2": network_caip2,
        "facilitator_url": facilitator_url,
        "max_timeout_seconds": 300,
        "name": payment_token.name,
        "version": payment_token.version,
    }


def _to_payment_amount(*, amount_minor: int, asset_decimals: int) -> int:
    exponent_delta = asset_decimals - _USD_MINOR_UNIT_EXPONENT
    if exponent_delta < 0:
        msg = "asset decimals cannot be less than USD minor unit precision"
        raise PaymentRequirementConfigError(msg)
    return amount_minor * (10**exponent_delta)
