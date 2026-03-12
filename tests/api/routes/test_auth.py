from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentActor
from app.db.session import get_db_session


class _DummySession:
    pass


async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
    yield cast("AsyncSession", _DummySession())


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    account_exists: bool,
) -> TestClient:
    import app.api.deps.auth as auth_module

    async def fake_exists(
        self: auth_module.AccountRepository,
        account_id: int,
    ) -> bool:
        _ = self
        _ = account_id
        return account_exists

    monkeypatch.setattr(auth_module.AccountRepository, "exists", fake_exists)

    app = FastAPI()

    @app.get("/protected")
    async def read_protected(actor: CurrentActor) -> dict[str, int]:
        return {"account_id": actor.account_id}

    app.dependency_overrides[get_db_session] = _override_get_db_session
    return TestClient(app)


def test_auth_dependency_rejects_missing_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_client(monkeypatch, account_exists=True) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.json() == {"detail": "X-Account-Id header is required"}


@pytest.mark.parametrize("header_value", ["abc", "0", "-3"])
def test_auth_dependency_rejects_invalid_account_id_header(
    monkeypatch: pytest.MonkeyPatch,
    header_value: str,
) -> None:
    with _build_client(monkeypatch, account_exists=True) as client:
        response = client.get(
            "/protected",
            headers={"X-Account-Id": header_value},
        )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "X-Account-Id must be a positive integer",
    }


def test_auth_dependency_rejects_unknown_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_client(monkeypatch, account_exists=False) as client:
        response = client.get(
            "/protected",
            headers={"X-Account-Id": "42"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "authenticated account does not exist"}


def test_auth_dependency_returns_actor_context_for_known_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _build_client(monkeypatch, account_exists=True) as client:
        response = client.get(
            "/protected",
            headers={"X-Account-Id": "42"},
        )

    assert response.status_code == 200
    assert response.json() == {"account_id": 42}
