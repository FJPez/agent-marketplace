import pytest
from tests.fixtures.settings import MALFORMED_REDIS_URL

from app.core.config import Settings
from app.core.resources import open_resources


async def test_open_resources_disposes_the_engine_when_a_later_resource_fails_to_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed = False

    class _FakeEngine:
        async def dispose(self) -> None:
            nonlocal disposed
            disposed = True

    monkeypatch.setattr("app.core.resources.create_engine", lambda settings: _FakeEngine())

    with pytest.raises(ValueError, match="Port out of range"):
        async with open_resources(Settings(redis_url=MALFORMED_REDIS_URL)):
            pass

    assert disposed is True
