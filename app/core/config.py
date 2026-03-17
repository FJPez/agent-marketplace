from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import AppEnv


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
    x402_pay_to_address: str | None = None
    payouts_enabled: bool = False
    payouts_rpc_url: str | None = None
    payouts_chain_id: int = 84532
    payouts_usdc_address: str | None = None
    payouts_wallet_private_key: SecretStr | None = None
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
        if self.payouts_enabled:
            missing = [
                field_name
                for field_name, value in (
                    ("payouts_rpc_url", self.payouts_rpc_url),
                    ("payouts_usdc_address", self.payouts_usdc_address),
                    (
                        "payouts_wallet_private_key",
                        None
                        if self.payouts_wallet_private_key is None
                        else self.payouts_wallet_private_key.get_secret_value(),
                    ),
                )
                if not value
            ]
            if missing:
                msg = f"payout settings are required when payouts are enabled: {', '.join(missing)}"
                raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
