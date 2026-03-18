from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from app.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping
    from pathlib import Path

TEST_ENV_FILE = ".env.test"
TEST_JWT_SECRET_KEY = "test-secret-key-with-32-bytes-123"
TEST_SIWE_DOMAIN = "testserver"


@pytest.fixture(scope="session", autouse=True)
def base_test_env() -> Generator[None, None, None]:
    original_values = {
        "APP_JWT_SECRET_KEY": os.environ.get("APP_JWT_SECRET_KEY"),
        "APP_SIWE_DOMAIN": os.environ.get("APP_SIWE_DOMAIN"),
        "APP_ENV_FILE": os.environ.get("APP_ENV_FILE"),
    }

    os.environ["APP_JWT_SECRET_KEY"] = TEST_JWT_SECRET_KEY
    os.environ["APP_SIWE_DOMAIN"] = TEST_SIWE_DOMAIN
    os.environ["APP_ENV_FILE"] = TEST_ENV_FILE
    get_settings.cache_clear()

    try:
        yield
    finally:
        for key, value in original_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


@pytest.fixture
def settings_env_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Callable[..., Path]:
    def configure(
        *,
        env: Mapping[str, str | None] | None = None,
        include_defaults: bool = True,
        cwd: Path | None = None,
    ) -> Path:
        monkeypatch.chdir(cwd or tmp_path)
        if include_defaults:
            monkeypatch.setenv("APP_JWT_SECRET_KEY", TEST_JWT_SECRET_KEY)
            monkeypatch.setenv("APP_SIWE_DOMAIN", TEST_SIWE_DOMAIN)
            monkeypatch.setenv("APP_ENV_FILE", TEST_ENV_FILE)

        for key, value in (env or {}).items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)

        get_settings.cache_clear()
        return cwd or tmp_path

    return configure
