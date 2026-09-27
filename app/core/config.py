import os
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.core.enums import AppEnv

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
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800
    db_statement_timeout_ms: int = 30000
    db_lock_timeout_ms: int = 5000
    db_idle_in_transaction_session_timeout_ms: int = 60000
    db_application_name: str = "agent-marketplace-api"
    redis_url: str | None = None
    api_rate_limit: str = "120/minute"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
