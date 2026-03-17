from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import AppEnv, Settings, get_settings


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
    monkeypatch.setenv("APP_PAYMENT_TOKEN_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
    monkeypatch.setenv("APP_TREASURY_PRIVATE_KEY", "0x" + "cd" * 32)

    settings = Settings()

    assert isinstance(settings.treasury_private_key, SecretStr)
    assert settings.treasury_private_key.get_secret_value() == "0x" + "cd" * 32
    assert settings.treasury_address == "0x89AEF553A06ab0C3173e79DE1Ce241A9ed3b992C"
    assert settings.payment_token_address == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


def test_settings_reject_invalid_treasury_private_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_PAYOUTS_ENABLED", "true")
    monkeypatch.setenv("APP_PAYOUTS_RPC_URL", "http://localhost:8545")
    monkeypatch.setenv("APP_PAYMENT_TOKEN_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
    monkeypatch.setenv("APP_TREASURY_PRIVATE_KEY", "not-a-key")

    with pytest.raises(ValidationError, match="treasury_private_key"):
        Settings()


def test_settings_reject_payment_token_address_that_does_not_match_supported_network_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_JWT_SECRET_KEY", "test-secret-key-with-32-bytes-123")
    monkeypatch.setenv("APP_PAYMENT_TOKEN_ADDRESS", "0x" + "ab" * 20)

    with pytest.raises(ValidationError, match="payment_token_address"):
        Settings()
