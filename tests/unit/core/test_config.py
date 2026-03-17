from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import AppEnv, Settings, get_settings


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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_DATABASE_URL", database_url)

    settings = Settings()

    assert settings.database_url == expected_database_url


def test_settings_use_default_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.delenv("APP_X402_CDP_API_KEY_ID", raising=False)
    monkeypatch.delenv("APP_X402_CDP_API_KEY_SECRET", raising=False)
    settings = Settings()

    assert settings.env is AppEnv.DEV
    assert settings.title == "Agent Marketplace Backend"
    assert settings.debug is False
    assert settings.jwt_secret_key == "test-secret-key-with-32-bytes-123"
    assert settings.jwt_access_token_expiry == 900
    assert settings.jwt_refresh_token_expiry == 604800
    assert settings.siwe_domain == "testserver"
    assert settings.siwe_nonce_expiry == 300
    assert settings.wallet_change_cooldown == 604800
    assert settings.api_key_prefix == "amp_"
    assert settings.x402_cdp_api_key_id is None
    assert settings.x402_cdp_api_key_secret is None


def test_settings_require_jwt_secret_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_JWT_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError, match="jwt_secret_key"):
        Settings()


def test_get_settings_allow_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_DEBUG", "true")

    settings = get_settings()

    assert settings.env is AppEnv.TEST
    assert settings.debug is True
    get_settings.cache_clear()


def test_settings_store_treasury_private_key_as_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_PAYOUTS_ENABLED", "true")
    monkeypatch.setenv("APP_PAYOUTS_RPC_URL", "http://localhost:8545")
    monkeypatch.setenv("APP_TREASURY_PRIVATE_KEY", "0x" + "cd" * 32)

    settings = Settings()

    assert isinstance(settings.treasury_private_key, SecretStr)
    assert settings.treasury_private_key.get_secret_value() == "0x" + "cd" * 32
    assert settings.treasury_address == "0x89AEF553A06ab0C3173e79DE1Ce241A9ed3b992C"
    assert settings.payment_token is not None
    assert settings.payment_token.address == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    assert settings.payment_token.symbol == "USDC"


def test_settings_reject_invalid_treasury_private_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_PAYOUTS_ENABLED", "true")
    monkeypatch.setenv("APP_PAYOUTS_RPC_URL", "http://localhost:8545")
    monkeypatch.setenv("APP_TREASURY_PRIVATE_KEY", "not-a-key")

    with pytest.raises(ValidationError, match="treasury_private_key"):
        Settings()


def test_settings_derive_payment_token_from_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_X402_NETWORK_CAIP2", "eip155:8453")

    settings = Settings()

    assert settings.payment_token is not None
    assert settings.payment_token.address == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert settings.payment_token.symbol == "USDC"


def test_settings_ignore_local_dotenv_without_explicit_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            [
                "APP_JWT_SECRET_KEY=dotenv-secret-key-with-32-bytes-minimum",
                "APP_SIWE_DOMAIN=127.0.0.1",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_ENV_FILE", raising=False)
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")

    settings = Settings()

    assert settings.jwt_secret_key == "test-secret-key-with-32-bytes-123"
    assert settings.siwe_domain == "testserver"


def test_settings_reject_debug_in_deployment_environments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("APP_DEBUG", "true")
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+asyncpg://db.example.com/app")
    monkeypatch.setenv("APP_SIWE_DOMAIN", "marketplace.example.com")

    with pytest.raises(ValidationError, match="debug must be false"):
        Settings()


def test_settings_require_non_local_database_in_deployment_environments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv(
        "APP_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace",
    )
    monkeypatch.setenv("APP_SIWE_DOMAIN", "staging.example.com")

    with pytest.raises(ValidationError, match="database_url must point to a non-local database"):
        Settings()


def test_settings_require_non_default_siwe_domain_in_deployment_environments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+asyncpg://db.example.com/app")
    monkeypatch.setenv("APP_SIWE_DOMAIN", "testserver")

    with pytest.raises(ValidationError, match="siwe_domain must be explicitly set"):
        Settings()


@pytest.mark.parametrize("payouts_enabled_value", [None, "false"])
def test_settings_require_payouts_enabled_in_deployment_environments(
    payouts_enabled_value: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+asyncpg://db.example.com/app")
    monkeypatch.setenv("APP_SIWE_DOMAIN", "marketplace.example.com")
    if payouts_enabled_value is None:
        monkeypatch.delenv("APP_PAYOUTS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("APP_PAYOUTS_ENABLED", payouts_enabled_value)

    with pytest.raises(ValidationError, match="payouts_enabled must be true"):
        Settings()


def test_settings_require_payout_rpc_url_in_deployment_environments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv(
        "APP_DATABASE_URL", "postgresql+asyncpg://db.internal:5432/agent_marketplace"
    )
    monkeypatch.setenv("APP_SIWE_DOMAIN", "staging.example.com")
    monkeypatch.setenv("APP_PAYOUTS_ENABLED", "true")
    monkeypatch.delenv("APP_PAYOUTS_RPC_URL", raising=False)
    monkeypatch.setenv("APP_TREASURY_PRIVATE_KEY", "0x" + "cd" * 32)

    with pytest.raises(ValidationError, match="payouts_rpc_url"):
        Settings()


def test_settings_require_treasury_private_key_in_deployment_environments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+asyncpg://db.example.com/app")
    monkeypatch.setenv("APP_SIWE_DOMAIN", "marketplace.example.com")
    monkeypatch.setenv("APP_PAYOUTS_ENABLED", "true")
    monkeypatch.setenv("APP_PAYOUTS_RPC_URL", "https://rpc.example.com")
    monkeypatch.delenv("APP_TREASURY_PRIVATE_KEY", raising=False)

    with pytest.raises(ValidationError, match="treasury_private_key"):
        Settings()


def test_settings_require_cdp_credentials_in_deployment_environments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+asyncpg://db.example.com/app")
    monkeypatch.setenv("APP_SIWE_DOMAIN", "marketplace.example.com")
    monkeypatch.setenv("APP_PAYOUTS_ENABLED", "true")
    monkeypatch.setenv("APP_PAYOUTS_RPC_URL", "https://rpc.example.com")
    monkeypatch.setenv("APP_TREASURY_PRIVATE_KEY", "0x" + "cd" * 32)
    monkeypatch.setenv("APP_X402_FACILITATOR_URL", "https://api.cdp.coinbase.com/platform/v2/x402")
    monkeypatch.delenv("APP_X402_CDP_API_KEY_ID", raising=False)
    monkeypatch.delenv("APP_X402_CDP_API_KEY_SECRET", raising=False)

    with pytest.raises(ValidationError, match="x402_cdp_api_key_id and x402_cdp_api_key_secret"):
        Settings()


def test_settings_accept_valid_deployment_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://db.internal:5432/agent_marketplace")
    monkeypatch.setenv("APP_SIWE_DOMAIN", "staging.example.com")
    monkeypatch.setenv("APP_PAYOUTS_ENABLED", "true")
    monkeypatch.setenv("APP_PAYOUTS_RPC_URL", "https://rpc.example.com")
    monkeypatch.setenv("APP_TREASURY_PRIVATE_KEY", "0x" + "cd" * 32)

    settings = Settings()

    assert settings.env is AppEnv.STAGING
    assert settings.debug is False
    assert settings.database_url == "postgresql+asyncpg://db.internal:5432/agent_marketplace"
    assert settings.payouts_enabled is True
