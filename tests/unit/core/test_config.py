import pytest

from app.core.config import Settings


def test_settings_use_default_values() -> None:
    settings = Settings()

    assert settings.title == "Agent Marketplace Backend"
    assert settings.debug is False


def test_settings_allow_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_TITLE", "Bootstrap Test")
    monkeypatch.setenv("APP_DEBUG", "true")

    settings = Settings()

    assert settings.title == "Bootstrap Test"
    assert settings.debug is True
