class PaymentRequirementConfigError(Exception):
    pass


_ASSET_CONFIG_BY_NETWORK: dict[str, dict[str, str]] = {
    "eip155:8453": {
        "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "name": "USD Coin",
        "version": "2",
        "decimals": "6",
    },
    "eip155:84532": {
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "name": "USDC",
        "version": "2",
        "decimals": "6",
    },
}

_USD_MINOR_UNIT_EXPONENT = 2


def build_payment_requirement(
    *,
    amount_minor: int,
    currency: str | None,
    treasury_address: str | None,
    facilitator_url: str,
    network: str,
    network_caip2: str,
) -> dict[str, object]:
    if treasury_address is None:
        raise PaymentRequirementConfigError(
            "payment configuration is incomplete: "
            "APP_TREASURY_PRIVATE_KEY is required for paid invokes",
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
        "payment_amount": _to_payment_amount(
            amount_minor=amount_minor,
            asset_decimals=int(asset_config["decimals"]),
        ),
        "currency": currency,
        "pay_to": treasury_address,
        "network": network,
        "network_caip2": network_caip2,
        "facilitator_url": facilitator_url,
        "max_timeout_seconds": 300,
        "name": asset_config["name"],
        "version": asset_config["version"],
    }


def _to_payment_amount(*, amount_minor: int, asset_decimals: int) -> int:
    exponent_delta = asset_decimals - _USD_MINOR_UNIT_EXPONENT
    if exponent_delta < 0:
        msg = "asset decimals cannot be less than USD minor unit precision"
        raise PaymentRequirementConfigError(msg)
    return amount_minor * (10**exponent_delta)
