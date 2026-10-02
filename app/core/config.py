import os
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import AfterValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.core.enums import AppEnv
from app.core.security import checksum_address

_LOCAL_DATABASE_HOSTS = {"127.0.0.1", "::1", "localhost"}
_DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace"
_DEFAULT_SIWE_DOMAIN = "testserver"
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _reject_zero_address(value: str) -> str:
    # A common template placeholder: USDC transfers to it revert, so every paid
    # call would fail at settlement, after the consumer signed, not at startup.
    if value == _ZERO_ADDRESS:
        msg = "address must not be the zero address"
        raise ValueError(msg)
    return value


# A payment address (the treasury, the asset contract) in its EIP-55 form.
EvmAddress = Annotated[
    str,
    AfterValidator(checksum_address),
    AfterValidator(_reject_zero_address),
]


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
        # Several settings are secrets (keys, the JWT secret): a startup error names the
        # setting and the rule it broke, never the value.
        hide_input_in_errors=True,
    )

    env: AppEnv = AppEnv.DEV
    title: str = "Agent Marketplace Backend"
    debug: bool = False
    database_url: str = _DEFAULT_DATABASE_URL
    jwt_secret_key: SecretStr = SecretStr("")
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
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    worker_shutdown_timeout_seconds: float = Field(default=25.0, gt=0)
    # Payment terms stamped on every new price version (spec section 12). The
    # treasury is every paid listing's payTo; staging and prod refuse to start
    # without one, and without one elsewhere no paid price can be set.
    treasury_address: EvmAddress | None = None
    # CAIP-2 allows a chain reference of at most 32 characters.
    payment_network: str = Field(default="eip155:84532", pattern=r"^eip155:[1-9][0-9]{0,31}$")
    # USDC on Base Sepolia, as listed by Circle at
    # https://developers.circle.com/stablecoins/usdc-contract-addresses
    payment_asset: EvmAddress = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    # In atomic units of payment_asset: 10000 is 0.01 USDC.
    min_price_amount: int = Field(default=10_000, gt=0)
    platform_fee_bps: int = Field(default=1_000, ge=0, le=10_000)
    # The validity window of a payment (x402 maxTimeoutSeconds): at most one hour.
    payment_max_timeout_seconds: int = Field(default=120, gt=0, le=3600)
    # Fernet keys that encrypt provider signing secrets at rest, comma-separated. The
    # first encrypts and every key decrypts, so a new key goes first and a retired one
    # stays listed while a stored secret may still be encrypted with it. Staging and
    # prod refuse to start without one; elsewhere, without one no secret can be issued.
    provider_secret_encryption_keys: Annotated[tuple[SecretStr, ...], NoDecode] = ()
    # How long a rotated-out signing secret keeps signing beside its replacement.
    provider_secret_grace_seconds: int = Field(default=86_400, gt=0)
    # How long a provider has to sign a payout address challenge.
    payout_address_challenge_seconds: int = Field(default=300, gt=0)
    # How long payouts are held after every payout address proof, even one of the
    # address already in use. It covers a change the owner notices and answers by
    # proving its own address again, which restarts the hold. It does not stop an
    # account takeover: see the payout address notes in README.md.
    payout_address_hold_seconds: int = Field(default=86_400, gt=0)
    # Request bodies are validated in this many worker processes, each validation within
    # this deadline (app/core/request_validation.py). A caller also waits at most the
    # deadline for a free worker. A worker starts at about 25 MiB and is replaced once it
    # passes 384 MiB; on Linux it can never pass 512 MiB, so an API process holds up to
    # this many times 512 MiB in workers.
    request_validation_timeout_ms: int = Field(default=250, gt=0, le=10_000)
    request_validation_workers: int = Field(default=2, gt=0, le=32)
    # A request schema is compiled in a worker when it is saved, within this deadline:
    # at most half the validation deadline (checked below), so a worker compiling a
    # stored schema afresh leaves at least half of it to validate the body.
    request_schema_compile_timeout_ms: int = Field(default=100, gt=0, le=5_000)
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

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        # Accept `APP_LOG_LEVEL=info`: the Literal above lists only upper-case names.
        return value.upper() if isinstance(value, str) else value

    @field_validator("provider_secret_encryption_keys", mode="before")
    @classmethod
    def split_provider_secret_encryption_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return [key.strip() for key in value.split(",") if key.strip()]
        return value

    @field_validator("provider_secret_encryption_keys")
    @classmethod
    def check_provider_secret_encryption_keys(
        cls,
        keys: tuple[SecretStr, ...],
    ) -> tuple[SecretStr, ...]:
        for key in keys:
            try:
                Fernet(key.get_secret_value())
            except ValueError:
                # Raised afresh so no part of a key reaches the startup error.
                msg = "provider_secret_encryption_keys must be Fernet keys"
                raise ValueError(msg) from None
        return keys

    @model_validator(mode="after")
    def validate_required_auth_settings(self) -> "Settings":
        self.database_url = normalize_database_url(self.database_url)
        if not self.jwt_secret_key.get_secret_value():
            msg = "jwt_secret_key is required"
            raise ValueError(msg)
        if self.env in {AppEnv.PROD, AppEnv.STAGING}:
            self._validate_deployment_settings()
        return self

    @model_validator(mode="after")
    def check_request_schema_compile_timeout(self) -> "Settings":
        if self.request_schema_compile_timeout_ms * 2 > self.request_validation_timeout_ms:
            msg = (
                "request_schema_compile_timeout_ms must be at most half of "
                "request_validation_timeout_ms"
            )
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
        if not self.redis_url:
            msg = "redis_url must be set when env is staging or prod"
            raise ValueError(msg)
        if self.treasury_address is None:
            msg = "treasury_address must be set when env is staging or prod"
            raise ValueError(msg)
        if not self.provider_secret_encryption_keys:
            msg = "provider_secret_encryption_keys must be set when env is staging or prod"
            raise ValueError(msg)


@lru_cache
def get_settings() -> Settings:
    return Settings()
