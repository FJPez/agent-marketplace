import pytest
from scripts import seed_demo
from tests.fixtures.settings import build_service_settings

from app.core.enums import AppEnv


@pytest.mark.parametrize("env", [AppEnv.STAGING, AppEnv.PROD])
async def test_the_demo_seed_refuses_a_deployed_environment(
    monkeypatch: pytest.MonkeyPatch,
    env: AppEnv,
) -> None:
    # It would create a live signing secret and print it.
    settings = build_service_settings().model_copy(update={"env": env})
    monkeypatch.setattr(seed_demo, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match=f"^the demo seed runs only in dev and test, not {env}$"):
        await seed_demo.seed_demo_data()
