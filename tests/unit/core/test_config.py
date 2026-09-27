from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from tests.fixtures.settings import TEST_JWT_SECRET_KEY, TEST_TREASURY_ADDRESS

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
    env: dict[str, str | None] = {
        "APP_ENV": "prod",
        "APP_JWT_SECRET_KEY": TEST_JWT_SECRET_KEY,
        "APP_DATABASE_URL": "postgresql+asyncpg://db.example.com/app",
        "APP_SIWE_DOMAIN": "marketplace.example.com",
        "APP_REDIS_URL": "redis://cache.internal:6379/0",
        "APP_TREASURY_ADDRESS": TEST_TREASURY_ADDRESS,
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
    settings_env_factory(env={"APP_TREASURY_ADDRESS": None})

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
    assert settings.api_key_touch_interval == 300
    assert settings.api_rate_limit == "120/minute"
    assert settings.log_level == "INFO"
    assert settings.worker_shutdown_timeout_seconds == 25.0
    assert settings.treasury_address is None
    assert settings.payment_network == "eip155:84532"
    assert settings.payment_asset == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    assert settings.min_price_amount == 10_000
    assert settings.platform_fee_bps == 1_000
    assert settings.payment_max_timeout_seconds == 120


@pytest.mark.parametrize(("log_level", "expected"), [("info", "INFO"), ("Warning", "WARNING")])
def test_settings_accept_a_log_level_in_any_case(
    settings_env_factory: SettingsEnvFactory,
    log_level: str,
    expected: str,
) -> None:
    settings_env_factory(env={"APP_LOG_LEVEL": log_level})

    assert Settings().log_level == expected


def test_settings_reject_an_unknown_log_level(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(env={"APP_LOG_LEVEL": "verbose"})

    with pytest.raises(ValidationError, match="log_level"):
        Settings()


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
            {"APP_REDIS_URL": None},
            "redis_url must be set",
            id="missing-redis-url",
        ),
        pytest.param(
            {"APP_TREASURY_ADDRESS": None},
            "treasury_address must be set",
            id="missing-treasury-address",
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


def test_settings_ignore_retired_payment_variables(
    settings_env_factory: SettingsEnvFactory,
    tmp_path: Path,
) -> None:
    retired_dotenv_path = tmp_path / ".env.retired"
    retired_dotenv_path.write_text(
        "\n".join(
            [
                "APP_X402_FACILITATOR_URL=https://api.cdp.coinbase.com/platform/v2/x402",
                "APP_PAYOUTS_ENABLED=false",
                "APP_TREASURY_PRIVATE_KEY=not-a-key",
                "APP_INVOKE_RATE_LIMIT=1/minute",
            ]
        ),
        encoding="utf-8",
    )
    settings_env_factory(env=_valid_deployment_env({"APP_ENV_FILE": str(retired_dotenv_path)}))

    settings = Settings()

    assert settings.env is AppEnv.PROD


@pytest.mark.parametrize(
    "env_overrides",
    [
        pytest.param({"APP_DB_STATEMENT_TIMEOUT_MS": "0"}, id="statement-timeout-zero"),
        pytest.param({"APP_DB_STATEMENT_TIMEOUT_MS": "-1"}, id="statement-timeout-negative"),
        pytest.param({"APP_DB_LOCK_TIMEOUT_MS": "0"}, id="lock-timeout-zero"),
        pytest.param({"APP_DB_LOCK_TIMEOUT_MS": "-1"}, id="lock-timeout-negative"),
        pytest.param(
            {"APP_DB_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS": "0"},
            id="idle-in-transaction-timeout-zero",
        ),
        pytest.param(
            {"APP_DB_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS": "-1"},
            id="idle-in-transaction-timeout-negative",
        ),
        pytest.param({"APP_API_KEY_TOUCH_INTERVAL": "-1"}, id="touch-interval-negative"),
        pytest.param(
            {"APP_WORKER_SHUTDOWN_TIMEOUT_SECONDS": "0"},
            id="worker-shutdown-timeout-zero",
        ),
    ],
)
def test_settings_reject_non_positive_timeout_and_touch_interval_settings(
    env_overrides: dict[str, str],
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(env=env_overrides)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "treasury_address",
    [
        "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
        "0xABCDEFABCDEFABCDEFABCDEFABCDEFABCDEFABCD",
        "0xABcdEFABcdEFabcdEfAbCdefabcdeFABcDEFabCD",
    ],
    ids=["lowercase", "uppercase", "checksummed"],
)
def test_settings_checksum_the_treasury_address(
    treasury_address: str,
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(env={"APP_TREASURY_ADDRESS": treasury_address})

    settings = Settings()

    assert settings.treasury_address == "0xABcdEFABcdEFabcdEfAbCdefabcdeFABcDEFabCD"


@pytest.mark.parametrize(
    "env_overrides",
    [
        pytest.param({"APP_TREASURY_ADDRESS": "0x1234"}, id="treasury-too-short"),
        pytest.param(
            {"APP_TREASURY_ADDRESS": "0xABCDEFabcdefabcdefabcdefabcdefabcdefabcd"},
            id="treasury-bad-checksum",
        ),
        pytest.param({"APP_PAYMENT_ASSET": "usdc"}, id="asset-not-an-address"),
        pytest.param({"APP_PAYMENT_NETWORK": "base-sepolia"}, id="network-not-caip2"),
        pytest.param({"APP_PAYMENT_NETWORK": "solana:devnet"}, id="network-not-evm"),
        pytest.param({"APP_MIN_PRICE_AMOUNT": "0"}, id="min-price-zero"),
        pytest.param({"APP_PLATFORM_FEE_BPS": "-1"}, id="fee-negative"),
        pytest.param({"APP_PLATFORM_FEE_BPS": "10001"}, id="fee-above-100-percent"),
        pytest.param({"APP_PAYMENT_MAX_TIMEOUT_SECONDS": "0"}, id="max-timeout-zero"),
    ],
)
def test_settings_reject_invalid_payment_terms(
    env_overrides: dict[str, str],
    settings_env_factory: SettingsEnvFactory,
) -> None:
    settings_env_factory(env=env_overrides)

    with pytest.raises(ValidationError):
        Settings()
