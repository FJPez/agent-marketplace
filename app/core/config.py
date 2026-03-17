import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from eth_account import Account
from pydantic import SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

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
_LOCAL_DATABASE_HOSTS = {"127.0.0.1", "::1", "localhost"}
_DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace"
_DEFAULT_SIWE_DOMAIN = "testserver"
_CDP_FACILITATOR_HOST = "api.cdp.coinbase.com"


def get_supported_payment_token(network_caip2: str) -> PaymentToken | None:
    return _SUPPORTED_PAYMENT_TOKENS_BY_NETWORK.get(network_caip2)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
    )

    env: AppEnv = AppEnv.DEV
    title: str = "Agent Marketplace Backend"
    debug: bool = False
    database_url: str = _DEFAULT_DATABASE_URL
    jwt_secret_key: str = ""
    jwt_access_token_expiry: int = 900
    jwt_refresh_token_expiry: int = 604800
    siwe_domain: str = _DEFAULT_SIWE_DOMAIN
    siwe_nonce_expiry: int = 300
    wallet_change_cooldown: int = 604800
    api_key_prefix: str = "amp_"
    quote_ttl_seconds: int = 300
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 15.0
    http_write_timeout: float = 15.0
    http_pool_timeout: float = 5.0
    http_max_connections: int = 100
    http_max_keepalive_connections: int = 20
    x402_facilitator_url: str = "https://x402.org/facilitator"
    x402_network: str = "base-sepolia"
    x402_network_caip2: str = "eip155:84532"
    x402_cdp_api_key_id: str | None = None
    x402_cdp_api_key_secret: str | None = None
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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        _ = dotenv_settings
        env_file = os.environ.get("APP_ENV_FILE")
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if env_file:
            sources.append(
                DotEnvSettingsSource(
                    settings_cls,
                    env_file=env_file,
                    env_file_encoding="utf-8",
                )
            )
        sources.append(file_secret_settings)
        return tuple(sources)

    @model_validator(mode="after")
    def validate_required_auth_settings(self) -> "Settings":
        if not self.jwt_secret_key:
            msg = "jwt_secret_key is required"
            raise ValueError(msg)
        if self.env in {AppEnv.PROD, AppEnv.STAGING}:
            self._validate_deployment_settings()
        if self.treasury_private_key is not None:
            self._derive_treasury_address()
        if self.payouts_enabled:
            if self.payment_token is None:
                msg = "x402_network_caip2 is not supported for payments"
                raise ValueError(msg)
            missing = [
                field_name
                for field_name, value in (
                    ("payouts_rpc_url", self.payouts_rpc_url),
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

    def _validate_deployment_settings(self) -> None:
        if self.debug:
            msg = "debug must be false when env is staging or prod"
            raise ValueError(msg)
        if self.siwe_domain == _DEFAULT_SIWE_DOMAIN:
            msg = "siwe_domain must be explicitly set when env is staging or prod"
            raise ValueError(msg)

        parsed_database_url = urlsplit(self.database_url)
        database_host = parsed_database_url.hostname
        if self.database_url == _DEFAULT_DATABASE_URL or database_host in _LOCAL_DATABASE_HOSTS:
            msg = "database_url must point to a non-local database when env is staging or prod"
            raise ValueError(msg)

        facilitator_host = urlsplit(self.x402_facilitator_url).hostname
        cdp_credentials = (
            self.x402_cdp_api_key_id,
            self.x402_cdp_api_key_secret,
        )
        if facilitator_host == _CDP_FACILITATOR_HOST and not all(cdp_credentials):
            msg = (
                "x402_cdp_api_key_id and x402_cdp_api_key_secret are required when "
                "env is staging or prod and the CDP facilitator is configured"
            )
            raise ValueError(msg)

    @property
    def treasury_address(self) -> str | None:
        if self.treasury_private_key is None:
            return None
        return self._derive_treasury_address()

    @property
    def payment_token(self) -> PaymentToken | None:
        return get_supported_payment_token(self.x402_network_caip2)

    def _derive_treasury_address(self) -> str:
        assert self.treasury_private_key is not None
        try:
            return Account.from_key(self.treasury_private_key.get_secret_value()).address
        except (TypeError, ValueError) as exc:
            msg = "treasury_private_key is invalid"
            raise ValueError(msg) from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()
