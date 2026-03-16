from pathlib import Path

import pytest
from pydantic import ValidationError

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
