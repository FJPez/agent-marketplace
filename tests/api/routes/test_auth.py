from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.core.security import AuthTokenType, create_jwt

if TYPE_CHECKING:
    from tests.fixtures.auth import AuthClientFactory

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


def test_auth_dependency_rejects_missing_authorization_header(
    auth_client_factory: AuthClientFactory,
) -> None:
    with auth_client_factory(account=_FakeAccount(id=42)) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authorization header is required"}


def test_auth_dependency_rejects_non_bearer_authorization_header(
    auth_client_factory: AuthClientFactory,
) -> None:
    with auth_client_factory(account=_FakeAccount(id=42)) as client:
        response = client.get("/protected", headers={"Authorization": "Token abc"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Bearer token is required"}


def test_auth_dependency_rejects_invalid_access_token(
    auth_client_factory: AuthClientFactory,
) -> None:
    with auth_client_factory(account=_FakeAccount(id=42)) as client:
        response = client.get("/protected", headers=_auth_header("invalid"))

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid access token"}


def test_auth_dependency_rejects_unknown_account(
    auth_client_factory: AuthClientFactory,
) -> None:
    token = _issue_access_token(account_id=42)

    with auth_client_factory(account=None) as client:
        response = client.get("/protected", headers=_auth_header(token))

    assert response.status_code == 401
    assert response.json() == {"detail": "authenticated account does not exist"}


def test_auth_dependency_rejects_stale_token_version(
    auth_client_factory: AuthClientFactory,
) -> None:
    token = _issue_access_token(account_id=42, token_version=1)

    with auth_client_factory(account=_FakeAccount(id=42, token_version=2)) as client:
        response = client.get("/protected", headers=_auth_header(token))

    assert response.status_code == 401
    assert response.json() == {"detail": "access token is no longer valid"}


def test_auth_dependency_returns_actor_context_for_valid_access_token(
    auth_client_factory: AuthClientFactory,
) -> None:
    token = _issue_access_token(account_id=42, token_version=3)

    with auth_client_factory(
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
    auth_client_factory: AuthClientFactory,
) -> None:
    with auth_client_factory(
        account=_FakeAccount(id=42),
        api_key=_FakeApiKey(account_id=42),
    ) as client:
        response = client.get("/protected", headers=_auth_header("amp_test-key"))

    assert response.status_code == 200
    assert response.json()["account_id"] == 42


def test_auth_dependency_rejects_revoked_api_key(
    auth_client_factory: AuthClientFactory,
) -> None:
    with auth_client_factory(
        account=_FakeAccount(id=42),
        api_key=_FakeApiKey(account_id=42, revoked_at=datetime.now(UTC)),
    ) as client:
        response = client.get("/protected", headers=_auth_header("amp_test-key"))

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid api key"}


def test_auth_dependency_rejects_expired_api_key(
    auth_client_factory: AuthClientFactory,
) -> None:
    with auth_client_factory(
        account=_FakeAccount(id=42),
        api_key=_FakeApiKey(account_id=42, expires_at=datetime.now(UTC) - timedelta(seconds=1)),
    ) as client:
        response = client.get("/protected", headers=_auth_header("amp_test-key"))

    assert response.status_code == 401
    assert response.json() == {"detail": "api key has expired"}
