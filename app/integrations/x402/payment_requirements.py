class PaymentRequirementConfigError(Exception):
    pass


_ASSET_CONFIG_BY_NETWORK: dict[str, dict[str, str]] = {
    "eip155:8453": {
        "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "name": "USD Coin",
        "version": "2",
    },
    "eip155:84532": {
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "name": "USDC",
        "version": "2",
    },
}


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
    asset_config = _ASSET_CONFIG_BY_NETWORK.get(network_caip2)
    if asset_config is None:
        raise PaymentRequirementConfigError("payment network is not supported")

    return {
        "scheme": "exact",
        "asset": asset_config["asset"],
        "amount_minor": amount_minor,
        "currency": currency,
        "pay_to": pay_to_address,
        "network": network,
        "network_caip2": network_caip2,
        "facilitator_url": facilitator_url,
        "max_timeout_seconds": 300,
        "name": asset_config["name"],
        "version": asset_config["version"],
    }
