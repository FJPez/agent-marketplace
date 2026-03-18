from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import hash_api_key
from app.db.session import get_db_session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.deps.auth import CurrentActor

TEST_JWT_SECRET_KEY = "test-secret-key-with-32-bytes-123"


class _DummySession:
    async def commit(self) -> None:
        return None


async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
    yield _DummySession()


@pytest.fixture
def auth_client_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., TestClient]:
    def build(
        *,
        account: object | None,
        api_key: object | None = None,
        secret: str = TEST_JWT_SECRET_KEY,
    ) -> TestClient:
        import app.api.deps.auth as auth_module
        import app.services.auth_resolution_service as auth_resolution_module

        monkeypatch.setattr(
            auth_module,
            "get_settings",
            lambda: Settings(jwt_secret_key=secret),
        )

        async def fake_get(
            self: auth_resolution_module.AccountRepository,
            account_id: int,
        ) -> object | None:
            _ = self
            if account is not None and getattr(account, "id", None) == account_id:
                return account
            return None

        async def fake_get_by_hash(
            self: auth_resolution_module.ApiKeyRepository,
            key_hash: str,
        ) -> object | None:
            _ = self
            if api_key is not None and key_hash == hash_api_key("amp_test-key"):
                return api_key
            return None

        def fake_touch_last_used(
            self: auth_resolution_module.ApiKeyRepository,
            current_api_key: object,
        ) -> object:
            _ = self
            current_api_key.last_used_at = datetime.now(UTC)
            return current_api_key

        monkeypatch.setattr(auth_resolution_module.AccountRepository, "get", fake_get)
        monkeypatch.setattr(
            auth_resolution_module.ApiKeyRepository,
            "get_by_hash",
            fake_get_by_hash,
        )
        monkeypatch.setattr(
            auth_resolution_module.ApiKeyRepository,
            "touch_last_used",
            fake_touch_last_used,
        )

        app = FastAPI()

        @app.get("/protected")
        async def read_protected(actor: CurrentActor) -> dict[str, object]:
            return {
                "account_id": actor.account_id,
                "is_admin": actor.is_admin,
                "wallet_address": actor.wallet_address,
            }

        app.dependency_overrides[get_db_session] = _override_get_db_session
        return TestClient(app)

    return build
