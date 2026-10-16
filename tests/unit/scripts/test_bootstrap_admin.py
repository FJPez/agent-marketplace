import pytest
from scripts.bootstrap_admin import BootstrapAdminError, bootstrap_admin, main


async def test_bootstrap_admin_rejects_invalid_wallet_address() -> None:
    with pytest.raises(
        BootstrapAdminError,
        match="APP_BOOTSTRAP_ADMIN_WALLET is not a valid wallet address",
    ):
        await bootstrap_admin(
            database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/agent_marketplace",
            admin_wallet="not-a-wallet",
        )


def test_main_reports_missing_admin_wallet(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+asyncpg://unused:5432/unused")
    monkeypatch.delenv("APP_BOOTSTRAP_ADMIN_WALLET", raising=False)

    exit_code = main()

    assert exit_code == 1
    assert capsys.readouterr().err == "APP_BOOTSTRAP_ADMIN_WALLET is required\n"
