from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

from app.core.config import Settings, get_settings
from app.core.enums import AppEnv

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_ENV_FILE = ".env.test"
TEST_JWT_SECRET_KEY = "test-secret-key-with-32-bytes-123"
TEST_SIWE_DOMAIN = "testserver"
TEST_TREASURY_ADDRESS = "0x1111111111111111111111111111111111111111"
# A Fernet key for encrypting provider signing secrets in tests only.
TEST_PROVIDER_SECRET_ENCRYPTION_KEY = "hSHxHDOnrub2nJUV4ELrhQ8qKrhEFxJZrSwoqseKlZI="
# The payment terms a price version is stamped with under the Settings defaults and
# the test treasury.
TEST_PRICE_TERMS = {
    "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    "network": "eip155:84532",
    "pay_to": TEST_TREASURY_ADDRESS,
    "max_timeout_seconds": 120,
    "fee_bps": 1_000,
}
# coredis rejects this URL as soon as a client is built from it (the port is out of range).
MALFORMED_REDIS_URL = "redis://localhost:99999/0"
# Nothing listens on port 1, so connecting fails at once.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:1/agent_marketplace"
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1/0"


def build_service_settings() -> Settings:
    """Settings for calling services directly.

    The env is always test, whatever APP_ENV says, and the treasury is passed explicitly
    rather than left to the session-wide APP_TREASURY_ADDRESS.
    """
    return Settings(
        env=AppEnv.TEST,
        jwt_secret_key=TEST_JWT_SECRET_KEY,
        treasury_address=TEST_TREASURY_ADDRESS,
    )


class SettingsEnvFactory(Protocol):
    def __call__(
        self,
        *,
        env: Mapping[str, str | None] | None = ...,
        include_defaults: bool = ...,
        cwd: Path | None = ...,
    ) -> Path: ...


@pytest.fixture(scope="session", autouse=True)
def base_test_env() -> Generator[None, None, None]:
    original_values = {
        "APP_JWT_SECRET_KEY": os.environ.get("APP_JWT_SECRET_KEY"),
        "APP_SIWE_DOMAIN": os.environ.get("APP_SIWE_DOMAIN"),
        "APP_ENV_FILE": os.environ.get("APP_ENV_FILE"),
        "APP_TREASURY_ADDRESS": os.environ.get("APP_TREASURY_ADDRESS"),
        "APP_PROVIDER_SECRET_ENCRYPTION_KEYS": os.environ.get(
            "APP_PROVIDER_SECRET_ENCRYPTION_KEYS",
        ),
    }

    os.environ["APP_JWT_SECRET_KEY"] = TEST_JWT_SECRET_KEY
    os.environ["APP_SIWE_DOMAIN"] = TEST_SIWE_DOMAIN
    os.environ["APP_ENV_FILE"] = TEST_ENV_FILE
    os.environ["APP_TREASURY_ADDRESS"] = TEST_TREASURY_ADDRESS
    os.environ["APP_PROVIDER_SECRET_ENCRYPTION_KEYS"] = TEST_PROVIDER_SECRET_ENCRYPTION_KEY
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
) -> Generator[SettingsEnvFactory, None, None]:
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
            monkeypatch.setenv("APP_TREASURY_ADDRESS", TEST_TREASURY_ADDRESS)
            monkeypatch.setenv(
                "APP_PROVIDER_SECRET_ENCRYPTION_KEYS",
                TEST_PROVIDER_SECRET_ENCRYPTION_KEY,
            )

        for key, value in (env or {}).items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)

        get_settings.cache_clear()
        return cwd or tmp_path

    yield configure
    # monkeypatch restores the environment, but a Settings built under the test's
    # environment would otherwise stay cached for the next test on this worker.
    get_settings.cache_clear()
