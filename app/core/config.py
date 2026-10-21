import os
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.core.enums import AppEnv
from app.core.evm import PaymentAddress

_LOCAL_DATABASE_HOSTS = {"127.0.0.1", "::1", "localhost"}
_DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace"
_DEFAULT_SIWE_DOMAIN = "testserver"


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    return database_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
        # A startup error names the setting, never its value: some settings are secrets.
        hide_input_in_errors=True,
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
    api_key_touch_interval: int = Field(default=300, ge=0)
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800
    db_statement_timeout_ms: int = Field(default=30000, gt=0)
    db_lock_timeout_ms: int = Field(default=5000, gt=0)
    db_idle_in_transaction_session_timeout_ms: int = Field(default=60000, gt=0)
    db_application_name: str = "agent-marketplace-api"
    redis_url: str | None = None
    api_rate_limit: str = "120/minute"
    max_request_body_bytes: int = Field(default=1024 * 1024, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # Every paid listing's pay_to, read when a payment is requested.
    treasury_address: PaymentAddress | None = None
    # CAIP-2 allows a chain reference of at most 32 characters.
    payment_network: str = Field(default="eip155:84532", pattern=r"^eip155:[1-9][0-9]{0,31}$")
    # USDC on Base Sepolia: https://developers.circle.com/stablecoins/usdc-contract-addresses
    payment_asset: PaymentAddress = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    # In atomic units of payment_asset: 10000 is 0.01 USDC.
    min_price_amount: int = Field(default=10_000, gt=0)
    platform_fee_bps: int = Field(default=1_000, ge=0, le=10_000)
    # The Fernet key that encrypts provider signing secrets at rest.
    provider_secret_encryption_key: SecretStr = SecretStr("")
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
        sources.append(
            DotEnvSettingsSource(
                settings_cls,
                env_file=env_file or ".env",
                env_file_encoding="utf-8",
            )
        )
        sources.append(file_secret_settings)
        return tuple(sources)

    @model_validator(mode="after")
    def validate_required_auth_settings(self) -> "Settings":
        self.database_url = normalize_database_url(self.database_url)
        if not self.jwt_secret_key:
            msg = "jwt_secret_key is required"
            raise ValueError(msg)
        encryption_key = self.provider_secret_encryption_key.get_secret_value()
        if not encryption_key:
            msg = "provider_secret_encryption_key is required"
            raise ValueError(msg)
        # Raises ValueError for a malformed key.
        Fernet(encryption_key)
        if self.env in {AppEnv.PROD, AppEnv.STAGING}:
            self._validate_deployment_settings()
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
        if not self.redis_url:
            msg = "redis_url must be set when env is staging or prod"
            raise ValueError(msg)
        if self.treasury_address is None:
            msg = "treasury_address must be set when env is staging or prod"
            raise ValueError(msg)


@lru_cache
def get_settings() -> Settings:
    return Settings()
