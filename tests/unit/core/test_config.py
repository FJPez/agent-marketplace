import pytest

from app.core.config import AppEnv, Settings, get_settings


def test_settings_use_default_values() -> None:
    settings = Settings()

    assert settings.env is AppEnv.DEV
    assert settings.title == "Agent Marketplace Backend"
    assert settings.debug is False


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
