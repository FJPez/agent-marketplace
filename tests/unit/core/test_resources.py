import pytest
from tests.fixtures.settings import MALFORMED_REDIS_URL

import app.core.resources as resources_module
from app.core.config import Settings
from app.core.resources import open_resources
from app.integrations.providers.dns import DnsPythonResolver


async def test_open_resources_disposes_the_engine_when_a_later_resource_fails_to_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed = False

    class _FakeEngine:
        async def dispose(self) -> None:
            nonlocal disposed
            disposed = True

    monkeypatch.setattr(resources_module, "create_engine", lambda settings: _FakeEngine())

    with pytest.raises(ValueError, match="Port out of range"):
        async with open_resources(Settings(redis_url=MALFORMED_REDIS_URL)):
            pass

    assert disposed is True


async def test_open_resources_provides_the_system_resolver_bounded_at_5_seconds() -> None:
    async with open_resources(Settings()) as resources:
        resolver = resources.dns_resolver

    assert isinstance(resolver, DnsPythonResolver)
    # Each query gives up after 5 s, retries included.
    assert resolver._resolver.lifetime == 5.0
