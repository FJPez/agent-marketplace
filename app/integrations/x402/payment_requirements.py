class PaymentRequirementConfigError(Exception):
    pass


def build_payment_requirement(
    *,
    amount_minor: int,
    currency: str | None,
    pay_to_address: str | None,
    facilitator_url: str,
    network: str,
    network_caip2: str,
) -> dict[str, object]:
    if pay_to_address is None:
        raise PaymentRequirementConfigError(
            "payment configuration is incomplete: "
            "APP_X402_PAY_TO_ADDRESS is required for paid invokes",
        )
    if currency != "USD":
        raise PaymentRequirementConfigError("payment currency is not supported")

    return {
        "scheme": "exact",
        "asset": "usdc",
        "amount_minor": amount_minor,
        "currency": currency,
        "pay_to": pay_to_address,
        "network": network,
        "network_caip2": network_caip2,
        "facilitator_url": facilitator_url,
        "max_timeout_seconds": 300,
    }
