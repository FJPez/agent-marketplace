from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.core.config import Settings
from app.core.security import AuthTokenType, create_jwt, hash_api_key
from app.db.session import get_db_session

TEST_SECRET = "test-secret-key-with-32-bytes-123"
TEST_WALLET = "0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6"


@dataclass
class _FakeAccount:
    id: int
    is_admin: bool = False
    wallet_address: str = TEST_WALLET
    token_version: int = 1


@dataclass
class _FakeApiKey:
    account_id: int
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None


class _DummySession:
    async def commit(self) -> None:
        return None


async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
    yield _DummySession()


def _issue_access_token(
    *,
    account_id: int = 42,
    wallet_address: str = TEST_WALLET,
    token_version: int = 1,
) -> str:
    return create_jwt(
        secret_key=TEST_SECRET,
        account_id=account_id,
        wallet_address=wallet_address,
        token_version=token_version,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=900,
        now=datetime.now(UTC),
    )


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    account: _FakeAccount | None,
    api_key: _FakeApiKey | None = None,
) -> TestClient:
    import app.api.deps.auth as auth_module
    import app.services.auth_resolution_service as auth_resolution_module

    monkeypatch.setattr(
        auth_module,
        "get_settings",
        lambda: Settings(jwt_secret_key=TEST_SECRET),
    )

    async def fake_get(
        self: auth_resolution_module.AccountRepository,
        account_id: int,
    ) -> _FakeAccount | None:
        _ = self
        if account is not None and account.id == account_id:
            return account
        return None

    monkeypatch.setattr(auth_resolution_module.AccountRepository, "get", fake_get)

    async def fake_get_by_hash(
        self: auth_resolution_module.ApiKeyRepository,
        key_hash: str,
    ) -> _FakeApiKey | None:
        _ = self
        if api_key is not None and key_hash == hash_api_key("amp_test-key"):
            return api_key
        return None

    def fake_touch_last_used(
        self: auth_resolution_module.ApiKeyRepository,
        current_api_key: _FakeApiKey,
    ) -> _FakeApiKey:
        _ = self
        current_api_key.last_used_at = datetime.now(UTC)
        return current_api_key

    monkeypatch.setattr(auth_resolution_module.ApiKeyRepository, "get_by_hash", fake_get_by_hash)
    monkeypatch.setattr(
        auth_resolution_module.ApiKeyRepository, "touch_last_used", fake_touch_last_used
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


def test_auth_dependency_rejects_missing_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_client(monkeypatch, account=_FakeAccount(id=42)) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authorization header is required"}


def test_auth_dependency_rejects_non_bearer_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_client(monkeypatch, account=_FakeAccount(id=42)) as client:
        response = client.get("/protected", headers={"Authorization": "Token abc"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Bearer token is required"}


def test_auth_dependency_rejects_invalid_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_client(monkeypatch, account=_FakeAccount(id=42)) as client:
        response = client.get("/protected", headers=_auth_header("invalid"))

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid access token"}


def test_auth_dependency_rejects_unknown_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _issue_access_token(account_id=42)

    with _build_client(monkeypatch, account=None) as client:
        response = client.get("/protected", headers=_auth_header(token))

    assert response.status_code == 401
    assert response.json() == {"detail": "authenticated account does not exist"}


def test_auth_dependency_rejects_stale_token_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _issue_access_token(account_id=42, token_version=1)

    with _build_client(monkeypatch, account=_FakeAccount(id=42, token_version=2)) as client:
        response = client.get("/protected", headers=_auth_header(token))

    assert response.status_code == 401
    assert response.json() == {"detail": "access token is no longer valid"}


def test_auth_dependency_returns_actor_context_for_valid_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _issue_access_token(account_id=42, token_version=3)

    with _build_client(
        monkeypatch,
        account=_FakeAccount(id=42, is_admin=True, token_version=3),
    ) as client:
        response = client.get("/protected", headers=_auth_header(token))

    assert response.status_code == 200
    assert response.json() == {
        "account_id": 42,
        "is_admin": True,
        "wallet_address": TEST_WALLET,
    }


def test_auth_dependency_accepts_valid_api_key_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_client(
        monkeypatch,
        account=_FakeAccount(id=42),
        api_key=_FakeApiKey(account_id=42),
    ) as client:
        response = client.get("/protected", headers=_auth_header("amp_test-key"))

    assert response.status_code == 200
    assert response.json()["account_id"] == 42


def test_auth_dependency_rejects_revoked_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_client(
        monkeypatch,
        account=_FakeAccount(id=42),
        api_key=_FakeApiKey(account_id=42, revoked_at=datetime.now(UTC)),
    ) as client:
        response = client.get("/protected", headers=_auth_header("amp_test-key"))

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid api key"}


def test_auth_dependency_rejects_expired_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_client(
        monkeypatch,
        account=_FakeAccount(id=42),
        api_key=_FakeApiKey(account_id=42, expires_at=datetime.now(UTC) - timedelta(seconds=1)),
    ) as client:
        response = client.get("/protected", headers=_auth_header("amp_test-key"))

    assert response.status_code == 401
    assert response.json() == {"detail": "api key has expired"}
