from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr, ValidationError
from tests.fixtures.settings import TEST_JWT_SECRET_KEY

from app.core.config import AppEnv, Settings, get_settings

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.settings import SettingsEnvFactory


def _write_dotenv(path: Path, *, jwt_secret: str, siwe_domain: str) -> None:
    path.write_text(
        "\n".join(
            [
                f"APP_JWT_SECRET_KEY={jwt_secret}",
                f"APP_SIWE_DOMAIN={siwe_domain}",
            ]
        ),
        encoding="utf-8",
    )


def _valid_deployment_env(
    overrides: dict[str, str | None] | None = None,
) -> dict[str, str | None]:
    env = {
        "APP_ENV": "prod",
        "APP_JWT_SECRET_KEY": TEST_JWT_SECRET_KEY,
        "APP_DATABASE_URL": "postgresql+asyncpg://db.example.com/app",
        "APP_SIWE_DOMAIN": "marketplace.example.com",
        "APP_REDIS_URL": "redis://cache.internal:6379/0",
        "APP_PAYOUTS_ENABLED": "true",
        "APP_PAYOUTS_RPC_URL": "https://rpc.example.com",
        "APP_TREASURY_PRIVATE_KEY": "0x" + "cd" * 32,
    }
    env.update(overrides or {})
    return env


@pytest.mark.parametrize(
    ("database_url", "expected_database_url"),
    [
        (
            "postgresql://db.example.com/agent_marketplace",
            "postgresql+asyncpg://db.example.com/agent_marketplace",
        ),
        (
            "postgres://db.example.com/agent_marketplace",
            "postgresql+asyncpg://db.example.com/agent_marketplace",
        ),
    ],
)
def test_settings_normalize_plain_postgres_database_urls(
    database_url: str,
    expected_database_url: str,
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(env={"APP_DATABASE_URL": database_url})

    settings = Settings()

    assert settings.database_url == expected_database_url


def test_settings_use_default_values(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(
        env={
            "APP_X402_CDP_API_KEY_ID": None,
            "APP_X402_CDP_API_KEY_SECRET": None,
        }
    )

    settings = Settings()

    assert settings.env is AppEnv.DEV
    assert settings.title == "Agent Marketplace Backend"
    assert settings.debug is False
    assert settings.jwt_secret_key == TEST_JWT_SECRET_KEY
    assert settings.jwt_access_token_expiry == 900
    assert settings.jwt_refresh_token_expiry == 604800
    assert settings.siwe_domain == "testserver"
    assert settings.siwe_nonce_expiry == 300
    assert settings.wallet_change_cooldown == 604800
    assert settings.api_key_prefix == "amp_"
    assert settings.x402_cdp_api_key_id is None
    assert settings.x402_cdp_api_key_secret is None


def test_settings_require_jwt_secret_key(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(
        include_defaults=False,
        env={
            "APP_ENV_FILE": None,
            "APP_JWT_SECRET_KEY": None,
            "APP_SIWE_DOMAIN": None,
        },
    )

    with pytest.raises(ValidationError, match="jwt_secret_key"):
        Settings()


def test_get_settings_allow_environment_overrides(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    get_settings.cache_clear()
    settings_env_factory(env={"APP_ENV": "test", "APP_DEBUG": "true"})

    settings = get_settings()

    assert settings.env is AppEnv.TEST
    assert settings.debug is True
    get_settings.cache_clear()


def test_settings_store_treasury_private_key_as_secret(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(
        env={
            "APP_PAYOUTS_ENABLED": "true",
            "APP_PAYOUTS_RPC_URL": "http://localhost:8545",
            "APP_TREASURY_PRIVATE_KEY": "0x" + "cd" * 32,
        }
    )

    settings = Settings()

    assert isinstance(settings.treasury_private_key, SecretStr)
    assert settings.treasury_private_key.get_secret_value() == "0x" + "cd" * 32
    assert settings.treasury_address == "0x89AEF553A06ab0C3173e79DE1Ce241A9ed3b992C"
    assert settings.payment_token is not None
    assert settings.payment_token.address == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    assert settings.payment_token.symbol == "USDC"


def test_settings_reject_invalid_treasury_private_key(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(
        env={
            "APP_PAYOUTS_ENABLED": "true",
            "APP_PAYOUTS_RPC_URL": "http://localhost:8545",
            "APP_TREASURY_PRIVATE_KEY": "not-a-key",
        }
    )

    with pytest.raises(ValidationError, match="treasury_private_key"):
        Settings()


def test_settings_derive_payment_token_from_network(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(env={"APP_X402_NETWORK_CAIP2": "eip155:8453"})

    settings = Settings()

    assert settings.payment_token is not None
    assert settings.payment_token.address == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert settings.payment_token.symbol == "USDC"


def test_settings_load_local_dotenv_by_default(
    settings_env_factory: SettingsEnvFactory,
    tmp_path: Path,
) -> None:
    _write_dotenv(
        tmp_path / ".env",
        jwt_secret="dotenv-secret-key-with-32-bytes-minimum",
        siwe_domain="127.0.0.1",
    )
    settings_env_factory(
        include_defaults=False,
        env={
            "APP_ENV_FILE": None,
            "APP_JWT_SECRET_KEY": None,
            "APP_SIWE_DOMAIN": None,
        },
    )

    settings = Settings()

    assert settings.jwt_secret_key == "dotenv-secret-key-with-32-bytes-minimum"
    assert settings.siwe_domain == "127.0.0.1"


def test_settings_environment_variables_override_local_dotenv(
    settings_env_factory: SettingsEnvFactory,
    tmp_path: Path,
) -> None:
    _write_dotenv(
        tmp_path / ".env",
        jwt_secret="dotenv-secret-key-with-32-bytes-minimum",
        siwe_domain="127.0.0.1",
    )
    settings_env_factory(
        include_defaults=False,
        env={
            "APP_ENV_FILE": None,
            "APP_JWT_SECRET_KEY": "env-secret-key-with-32-bytes-minimum",
            "APP_SIWE_DOMAIN": "api.example.com",
        },
    )

    settings = Settings()

    assert settings.jwt_secret_key == "env-secret-key-with-32-bytes-minimum"
    assert settings.siwe_domain == "api.example.com"


def test_settings_use_app_env_file_instead_of_default_dotenv(
    settings_env_factory: SettingsEnvFactory,
    tmp_path: Path,
) -> None:
    _write_dotenv(
        tmp_path / ".env",
        jwt_secret="default-dotenv-secret-key-with-32-bytes",
        siwe_domain="default.example.com",
    )
    custom_dotenv_path = tmp_path / ".env.custom"
    _write_dotenv(
        custom_dotenv_path,
        jwt_secret="custom-dotenv-secret-key-with-32-bytes-okay",
        siwe_domain="custom.example.com",
    )
    settings_env_factory(
        include_defaults=False,
        env={
            "APP_ENV_FILE": str(custom_dotenv_path),
            "APP_JWT_SECRET_KEY": None,
            "APP_SIWE_DOMAIN": None,
        },
    )

    settings = Settings()

    assert settings.jwt_secret_key == "custom-dotenv-secret-key-with-32-bytes-okay"
    assert settings.siwe_domain == "custom.example.com"


@pytest.mark.parametrize(
    ("env_overrides", "match"),
    [
        pytest.param({"APP_DEBUG": "true"}, "debug must be false", id="debug"),
        pytest.param(
            {
                "APP_ENV": "staging",
                "APP_DATABASE_URL": (
                    "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace"
                ),
                "APP_SIWE_DOMAIN": "staging.example.com",
            },
            "database_url must point to a non-local database",
            id="local-database",
        ),
        pytest.param(
            {"APP_SIWE_DOMAIN": "testserver"},
            "siwe_domain must be explicitly set",
            id="default-siwe-domain",
        ),
        pytest.param(
            {"APP_PAYOUTS_ENABLED": None},
            "payouts_enabled must be true",
            id="missing-payouts-enabled",
        ),
        pytest.param(
            {"APP_PAYOUTS_ENABLED": "false"},
            "payouts_enabled must be true",
            id="false-payouts-enabled",
        ),
        pytest.param(
            {
                "APP_ENV": "staging",
                "APP_DATABASE_URL": "postgresql+asyncpg://db.internal:5432/agent_marketplace",
                "APP_SIWE_DOMAIN": "staging.example.com",
                "APP_PAYOUTS_RPC_URL": None,
            },
            "payouts_rpc_url",
            id="missing-payout-rpc-url",
        ),
        pytest.param(
            {"APP_TREASURY_PRIVATE_KEY": None},
            "treasury_private_key",
            id="missing-treasury-key",
        ),
        pytest.param(
            {"APP_REDIS_URL": None},
            "redis_url must be set",
            id="missing-redis-url",
        ),
        pytest.param(
            {
                "APP_X402_FACILITATOR_URL": "https://api.cdp.coinbase.com/platform/v2/x402",
                "APP_X402_CDP_API_KEY_ID": None,
                "APP_X402_CDP_API_KEY_SECRET": None,
            },
            "x402_cdp_api_key_id and x402_cdp_api_key_secret",
            id="missing-cdp-credentials",
        ),
    ],
)
def test_settings_validate_deployment_environment_requirements(
    env_overrides: dict[str, str | None],
    match: str,
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(env=_valid_deployment_env(env_overrides))

    with pytest.raises(ValidationError, match=match):
        Settings()


def test_settings_accept_valid_deployment_configuration(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(
        env=_valid_deployment_env(
            {
                "APP_ENV": "staging",
                "APP_DATABASE_URL": "postgresql://db.internal:5432/agent_marketplace",
                "APP_SIWE_DOMAIN": "staging.example.com",
            }
        )
    )

    settings = Settings()

    assert settings.env is AppEnv.STAGING
    assert settings.debug is False
    assert settings.database_url == "postgresql+asyncpg://db.internal:5432/agent_marketplace"
    assert settings.redis_url == "redis://cache.internal:6379/0"
    assert settings.payouts_enabled is True
