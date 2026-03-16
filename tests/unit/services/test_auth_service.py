from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.security import ParsedSiweMessage
from app.db.models import Account
from app.services.auth_service import AuthService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: list[object] = []

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, instance: object) -> None:
        self.refreshed.append(instance)


class FakeAccountRepo:
    def __init__(
        self,
        account: Account | None = None,
        *,
        create_error: Exception | None = None,
        reloaded_account: Account | None = None,
    ) -> None:
        self.account = account
        self.create_error = create_error
        self.reloaded_account = reloaded_account
        self.get_by_wallet_calls = 0
        self.created_account: Account | None = None

    async def get_by_wallet_address(self, wallet_address: str) -> Account | None:
        self.get_by_wallet_calls += 1
        if self.get_by_wallet_calls == 1:
            return self.account
        return self.reloaded_account

    async def get_by_wallet_address_for_update(self, wallet_address: str) -> Account | None:
        return await self.get_by_wallet_address(wallet_address)

    async def create(
        self,
        *,
        wallet_address: str,
        display_name: str = "Anonymous",
        account_type: str = "human",
        nonce: str = "",
        nonce_issued_at: datetime | None = None,
    ) -> Account:
        if self.create_error is not None:
            raise self.create_error

        account = Account(
            id=1,
            wallet_address=wallet_address,
            display_name=display_name,
            account_type=account_type,
            nonce=nonce,
            nonce_issued_at=nonce_issued_at or datetime.now(UTC),
            token_version=1,
            is_admin=False,
        )
        self.created_account = account
        return account

    def update_nonce(
        self,
        account: Account,
        *,
        nonce: str,
        issued_at: datetime | None = None,
    ) -> Account:
        account.nonce = nonce
        account.nonce_issued_at = issued_at or datetime.now(UTC)
        return account


def _settings() -> Settings:
    return Settings(
        jwt_secret_key="test-secret-key-with-32-bytes-123",
        siwe_domain="testserver",
        siwe_nonce_expiry=300,
    )


def _account(*, nonce: str = "nonce-1", issued_at: datetime | None = None) -> Account:
    return Account(
        id=1,
        wallet_address="0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6",
        account_type="human",
        display_name="Primary",
        nonce=nonce,
        nonce_issued_at=issued_at or datetime.now(UTC),
        token_version=1,
        is_admin=False,
    )


@pytest.mark.asyncio
async def test_issue_nonce_reuses_existing_unexpired_nonce() -> None:
    fake_session = FakeSession()
    account = _account(issued_at=datetime.now(UTC) - timedelta(seconds=30))
    service = AuthService(cast("AsyncSession", fake_session), settings=_settings())
    service._account_repo = FakeAccountRepo(account)

    assert account.wallet_address is not None
    nonce = await service.issue_nonce(wallet_address=account.wallet_address)

    assert nonce == "nonce-1"
    assert fake_session.commits == 0


@pytest.mark.asyncio
async def test_issue_nonce_recovers_from_duplicate_account_creation() -> None:
    fake_session = FakeSession()
    account = _account()
    service = AuthService(cast("AsyncSession", fake_session), settings=_settings())
    service._account_repo = FakeAccountRepo(
        None,
        create_error=IntegrityError("statement", {}, Exception("duplicate key")),
        reloaded_account=account,
    )

    assert account.wallet_address is not None
    nonce = await service.issue_nonce(wallet_address=account.wallet_address)

    assert nonce == "nonce-1"
    assert fake_session.commits == 0
    assert fake_session.rollbacks == 1


@pytest.mark.asyncio
async def test_verify_wallet_uses_locked_account_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = FakeSession()
    account = _account()
    service = AuthService(cast("AsyncSession", fake_session), settings=_settings())
    repo = FakeAccountRepo(account)
    service._account_repo = repo
    issued_at = datetime.now(UTC).replace(microsecond=0)
    locked_calls = 0

    async def fail_unlocked_lookup(wallet_address: str) -> Account | None:
        _ = wallet_address
        raise AssertionError("unlocked wallet lookup should not be used")

    async def locked_lookup(wallet_address: str) -> Account | None:
        nonlocal locked_calls
        _ = wallet_address
        locked_calls += 1
        return account

    def fake_parse(*, message: str, signature: str) -> ParsedSiweMessage:
        _ = message, signature
        return ParsedSiweMessage(
            domain="testserver",
            address=account.wallet_address or "",
            uri="http://testserver",
            version="1",
            chain_id=1,
            nonce=account.nonce,
            issued_at=issued_at,
        )

    repo.get_by_wallet_address = fail_unlocked_lookup  # type: ignore[method-assign]
    repo.get_by_wallet_address_for_update = locked_lookup  # type: ignore[method-assign]
    monkeypatch.setattr(service, "_parse_and_validate_message", fake_parse)

    result = await service.verify_wallet(message="ignored", signature="ignored")

    assert result.account.id == account.id
    assert locked_calls == 1
