from dataclasses import dataclass
from functools import lru_cache

from eth_account import Account
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from web3 import Web3

from app.core.enums import AppEnv


@dataclass(frozen=True, slots=True)
class PaymentToken:
    address: str
    name: str
    symbol: str
    decimals: int
    version: str


_SUPPORTED_PAYMENT_TOKENS_BY_NETWORK: dict[str, PaymentToken] = {
    "eip155:8453": PaymentToken(
        address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        name="USD Coin",
        symbol="USDC",
        decimals=6,
        version="2",
    ),
    "eip155:84532": PaymentToken(
        address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        name="USDC",
        symbol="USDC",
        decimals=6,
        version="2",
    ),
}


def get_supported_payment_token(network_caip2: str) -> PaymentToken | None:
    return _SUPPORTED_PAYMENT_TOKENS_BY_NETWORK.get(network_caip2)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
    )

    env: AppEnv = AppEnv.DEV
    title: str = "Agent Marketplace Backend"
    debug: bool = False
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace"
    jwt_secret_key: str = ""
    jwt_access_token_expiry: int = 900
    jwt_refresh_token_expiry: int = 604800
    siwe_domain: str = "testserver"
    siwe_nonce_expiry: int = 300
    wallet_change_cooldown: int = 604800
    api_key_prefix: str = "amp_"
    quote_ttl_seconds: int = 300
    x402_facilitator_url: str = "https://x402.org/facilitator"
    x402_network: str = "base-sepolia"
    x402_network_caip2: str = "eip155:84532"
    x402_cdp_api_key_id: str | None = None
    x402_cdp_api_key_secret: str | None = None
    payment_token_address: str | None = None
    payouts_enabled: bool = False
    payouts_rpc_url: str | None = None
    payouts_chain_id: int = 84532
    treasury_private_key: SecretStr | None = None
    api_rate_limit: str = "120/minute"
    invoke_rate_limit: str = "60/minute"
    quote_rate_limit: str = "30/minute"
    invoke_payload_max_bytes: int = 1024 * 1024
    demo_upstream_base_url: str = "https://provider.example.com"
    demo_free_upstream_path: str = "/demo/free-ping"
    demo_paid_upstream_path: str = "/demo/paid-summary"

    @model_validator(mode="after")
    def validate_required_auth_settings(self) -> "Settings":
        if not self.jwt_secret_key:
            msg = "jwt_secret_key is required"
            raise ValueError(msg)
        if self.treasury_private_key is not None:
            self._derive_treasury_address()
        if self.payment_token_address is not None:
            self._resolve_payment_token()
        if self.payouts_enabled:
            missing = [
                field_name
                for field_name, value in (
                    ("payouts_rpc_url", self.payouts_rpc_url),
                    ("payment_token_address", self.payment_token_address),
                    (
                        "treasury_private_key",
                        None
                        if self.treasury_private_key is None
                        else self.treasury_private_key.get_secret_value(),
                    ),
                )
                if not value
            ]
            if missing:
                msg = f"payout settings are required when payouts are enabled: {', '.join(missing)}"
                raise ValueError(msg)
        return self

    @property
    def treasury_address(self) -> str | None:
        if self.treasury_private_key is None:
            return None
        return self._derive_treasury_address()

    @property
    def payment_token(self) -> PaymentToken | None:
        if self.payment_token_address is None:
            return None
        return self._resolve_payment_token()

    def _derive_treasury_address(self) -> str:
        assert self.treasury_private_key is not None
        try:
            return Account.from_key(self.treasury_private_key.get_secret_value()).address
        except (TypeError, ValueError) as exc:
            msg = "treasury_private_key is invalid"
            raise ValueError(msg) from exc

    def _resolve_payment_token(self) -> PaymentToken:
        assert self.payment_token_address is not None
        supported_token = get_supported_payment_token(self.x402_network_caip2)
        if supported_token is None:
            msg = "payment_token_address is not supported on the configured x402 network"
            raise ValueError(msg)
        try:
            configured_token_address = Web3.to_checksum_address(self.payment_token_address)
        except ValueError as exc:
            msg = "payment_token_address is invalid"
            raise ValueError(msg) from exc
        if configured_token_address != supported_token.address:
            msg = "payment_token_address does not match the supported token for x402_network_caip2"
            raise ValueError(msg)
        return supported_token


@lru_cache
def get_settings() -> Settings:
    return Settings()
