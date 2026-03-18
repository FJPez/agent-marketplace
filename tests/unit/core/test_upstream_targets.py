import pytest
from tests.fixtures.settings import SettingsEnvFactory


def test_validate_upstream_base_url_accepts_loopback_http_in_test_env(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    from app.core.upstream_targets import validate_upstream_base_url

    settings_env_factory(env={"APP_ENV": "test"})

    assert validate_upstream_base_url("http://127.0.0.1:9000") == "http://127.0.0.1:9000"


def test_validate_upstream_base_url_rejects_loopback_http_in_prod(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    from app.core.upstream_targets import UnsafeUpstreamTargetError, validate_upstream_base_url

    settings_env_factory(
        env={
            "APP_ENV": "prod",
            "APP_DATABASE_URL": "postgresql+asyncpg://db.example.com:5432/agent_marketplace",
            "APP_REDIS_URL": "redis://cache.example.com:6379/0",
            "APP_PAYOUTS_ENABLED": "true",
            "APP_PAYOUTS_RPC_URL": "https://rpc.example.com",
            "APP_TREASURY_PRIVATE_KEY": (
                "0x59c6995e998f97a5a0044966f0945382d7f6b1f07296a3f80b5b85ddf0f0f001"
            ),
            "APP_SIWE_DOMAIN": "api.example.com",
        }
    )

    with pytest.raises(UnsafeUpstreamTargetError, match="upstream target is not allowed"):
        validate_upstream_base_url("http://127.0.0.1:9000")


def test_validate_upstream_base_url_rejects_private_https_targets(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    from app.core.upstream_targets import UnsafeUpstreamTargetError, validate_upstream_base_url

    settings_env_factory(env={"APP_ENV": "test"})

    with pytest.raises(UnsafeUpstreamTargetError, match="upstream target is not allowed"):
        validate_upstream_base_url("https://127.0.0.1:9000")


def test_validate_upstream_base_url_rejects_metadata_targets(
    settings_env_factory: SettingsEnvFactory,
) -> None:
    from app.core.upstream_targets import UnsafeUpstreamTargetError, validate_upstream_base_url

    settings_env_factory(env={"APP_ENV": "test"})

    with pytest.raises(UnsafeUpstreamTargetError, match="upstream target is not allowed"):
        validate_upstream_base_url("http://169.254.169.254/latest/meta-data")
