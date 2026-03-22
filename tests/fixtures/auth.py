from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Protocol, cast

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps.auth import get_current_actor
from app.api.exception_handlers import install_exception_handlers
from app.core.config import Settings
from app.core.security import hash_api_key
from app.db.session import get_db_session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

TEST_JWT_SECRET_KEY = "test-secret-key-with-32-bytes-123"
_CURRENT_ACTOR_DEPENDENCY = Depends(get_current_actor)


class AuthClientFactory(Protocol):
    def __call__(
        self,
        *,
        account: object | None,
        api_key: object | None = ...,
        secret: str = ...,
    ) -> TestClient: ...


class _ResolvedActor(Protocol):
    account_id: int
    is_admin: bool
    wallet_address: str


class _MutableApiKey(Protocol):
    last_used_at: datetime | None


class _DummySession:
    async def commit(self) -> None:
        return None


async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
    yield _DummySession()


@pytest.fixture
def auth_client_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AuthClientFactory:
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
            current_api_key: _MutableApiKey,
        ) -> _MutableApiKey:
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
        install_exception_handlers(app)

        @app.get("/protected")
        async def read_protected(
            actor: Annotated[object, _CURRENT_ACTOR_DEPENDENCY],
        ) -> dict[str, object]:
            resolved_actor = cast("_ResolvedActor", actor)
            return {
                "account_id": resolved_actor.account_id,
                "is_admin": resolved_actor.is_admin,
                "wallet_address": resolved_actor.wallet_address,
            }

        app.dependency_overrides[get_db_session] = _override_get_db_session
        return TestClient(app)

    return build
