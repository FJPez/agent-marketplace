from functools import lru_cache

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
    quote_ttl_seconds: int = 300
    x402_facilitator_url: str = "https://x402.org/facilitator"
    x402_network: str = "base-sepolia"
    x402_network_caip2: str = "eip155:84532"
    x402_pay_to_address: str | None = None
    demo_upstream_base_url: str = "https://provider.example.com"
    demo_free_upstream_path: str = "/demo/free-ping"
    demo_paid_upstream_path: str = "/demo/paid-summary"


@lru_cache
def get_settings() -> Settings:
    return Settings()
