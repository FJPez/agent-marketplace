from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.exc import IntegrityError

import app.services.wallet_change_service as wallet_change_module
from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.security import ParsedSiweMessage
from app.db.models import Account
from app.services.wallet_change_service import WalletChangeError, WalletChangeService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class FakeSession:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: list[object] = []
        self.commit_error = commit_error

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, instance: object) -> None:
        self.refreshed.append(instance)


class FakeAccountRepo:
    def __init__(self, account: Account, *, existing_wallet_owner: Account | None = None) -> None:
        self.account = account
        self.existing_wallet_owner = existing_wallet_owner

    async def get(self, account_id: int) -> Account | None:
        return self.account if self.account.id == account_id else None

    async def get_for_update(self, account_id: int) -> Account | None:
        return await self.get(account_id)

    async def get_by_wallet_address(self, wallet_address: str) -> Account | None:
        if (
            self.existing_wallet_owner is not None
            and self.existing_wallet_owner.wallet_address == wallet_address
        ):
            return self.existing_wallet_owner
        if self.account.wallet_address == wallet_address:
            return self.account
        return None

    def update_nonce(
        self, account: Account, *, nonce: str, issued_at: datetime | None = None
    ) -> Account:
        account.nonce = nonce
        account.nonce_issued_at = issued_at or datetime.now(UTC)
        return account

    def update_wallet(
        self,
        account: Account,
        *,
        wallet_address: str,
        wallet_changed_at: datetime | None = None,
    ) -> Account:
        account.wallet_address = wallet_address
        account.wallet_changed_at = wallet_changed_at or datetime.now(UTC)
        return account

    def bump_token_version(self, account: Account) -> Account:
        account.token_version += 1
        return account


class FakeWalletChangeLogRepo:
    def __init__(self) -> None:
        self.entries: list[tuple[int, str, str]] = []

    def add(
        self,
        *,
        account_id: int,
        previous_wallet_address: str,
        new_wallet_address: str,
    ) -> None:
        self.entries.append((account_id, previous_wallet_address, new_wallet_address))


def _settings() -> Settings:
    return Settings(
        jwt_secret_key="test-secret-key-with-32-bytes-123",
        siwe_domain="testserver",
        wallet_change_cooldown=604800,
        siwe_nonce_expiry=300,
    )


def _account() -> Account:
    return Account(
        id=1,
        wallet_address="0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6",
        account_type="human",
        display_name="Primary",
        nonce="nonce-1",
        nonce_issued_at=datetime.now(UTC),
        token_version=1,
        is_admin=False,
    )


@pytest.mark.asyncio
async def test_initiate_wallet_change_rejects_cooldown() -> None:
    fake_session = FakeSession()
    session = cast("AsyncSession", fake_session)
    account = _account()
    account.wallet_changed_at = datetime.now(UTC) - timedelta(days=1)
    service = WalletChangeService(session, settings=_settings())
    service._account_repo = FakeAccountRepo(account)

    with pytest.raises(WalletChangeError, match="cooldown"):
        await service.initiate_change(
            ActorContext(account_id=1),
            wallet_address="0x000000000000000000000000000000000000dEaD",
        )


@pytest.mark.asyncio
async def test_initiate_wallet_change_rotates_nonce() -> None:
    fake_session = FakeSession()
    session = cast("AsyncSession", fake_session)
    account = _account()
    service = WalletChangeService(session, settings=_settings())
    service._account_repo = FakeAccountRepo(account)

    challenge = await service.initiate_change(
        ActorContext(account_id=1),
        wallet_address="0x000000000000000000000000000000000000dEaD",
    )

    assert challenge.nonce == account.nonce
    assert fake_session.commits == 1


@pytest.mark.asyncio
async def test_confirm_wallet_change_rejects_duplicate_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = FakeSession()
    session = cast("AsyncSession", fake_session)
    account = _account()
    other_account = Account(
        id=2,
        wallet_address="0x000000000000000000000000000000000000dEaD",
        account_type="human",
        display_name="Other",
        nonce="nonce-2",
        nonce_issued_at=datetime.now(UTC),
        token_version=1,
        is_admin=False,
    )
    service = WalletChangeService(session, settings=_settings())
    service._account_repo = FakeAccountRepo(account, existing_wallet_owner=other_account)
    service._wallet_change_log_repo = FakeWalletChangeLogRepo()
    issued_at = datetime.now(UTC).replace(microsecond=0)

    def fake_verify(
        _settings: Settings,
        *,
        message: str,
        signature: str,
        expected_nonce: str,
        now: datetime | None = None,
    ) -> ParsedSiweMessage:
        _ = message, signature, expected_nonce, now
        assert other_account.wallet_address is not None
        return ParsedSiweMessage(
            domain="testserver",
            address=other_account.wallet_address,
            uri="http://testserver",
            version="1",
            chain_id=1,
            nonce="nonce-1",
            issued_at=issued_at,
        )

    monkeypatch.setattr(wallet_change_module, "verify_siwe_signature", fake_verify)

    with pytest.raises(WalletChangeError, match="already in use"):
        await service.confirm_change(
            ActorContext(account_id=1),
            message="ignored",
            signature="ignored",
        )


@pytest.mark.asyncio
async def test_confirm_wallet_change_translates_commit_uniqueness_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = FakeSession(
        commit_error=IntegrityError("statement", {}, Exception("duplicate key"))
    )
    session = cast("AsyncSession", fake_session)
    account = _account()
    service = WalletChangeService(session, settings=_settings())
    service._account_repo = FakeAccountRepo(account)
    service._wallet_change_log_repo = FakeWalletChangeLogRepo()
    issued_at = datetime.now(UTC).replace(microsecond=0)

    def fake_verify(
        _settings: Settings,
        *,
        message: str,
        signature: str,
        expected_nonce: str,
        now: datetime | None = None,
    ) -> ParsedSiweMessage:
        _ = message, signature, expected_nonce, now
        return ParsedSiweMessage(
            domain="testserver",
            address="0x000000000000000000000000000000000000dEaD",
            uri="http://testserver",
            version="1",
            chain_id=1,
            nonce="nonce-1",
            issued_at=issued_at,
        )

    monkeypatch.setattr(wallet_change_module, "verify_siwe_signature", fake_verify)

    with pytest.raises(WalletChangeError, match="already in use"):
        await service.confirm_change(
            ActorContext(account_id=1),
            message="ignored",
            signature="ignored",
        )


@pytest.mark.asyncio
async def test_confirm_wallet_change_uses_locked_account_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = FakeSession()
    session = cast("AsyncSession", fake_session)
    account = _account()
    service = WalletChangeService(session, settings=_settings())
    repo = FakeAccountRepo(account)
    service._account_repo = repo
    service._wallet_change_log_repo = FakeWalletChangeLogRepo()
    issued_at = datetime.now(UTC).replace(microsecond=0)
    locked_calls = 0

    async def fail_unlocked_lookup(account_id: int) -> Account | None:
        _ = account_id
        raise AssertionError("unlocked account lookup should not be used")

    async def locked_lookup(account_id: int) -> Account | None:
        nonlocal locked_calls
        assert account_id == account.id
        locked_calls += 1
        return account

    def fake_verify(
        _settings: Settings,
        *,
        message: str,
        signature: str,
        expected_nonce: str,
        now: datetime | None = None,
    ) -> ParsedSiweMessage:
        _ = message, signature, expected_nonce, now
        return ParsedSiweMessage(
            domain="testserver",
            address="0x000000000000000000000000000000000000dEaD",
            uri="http://testserver",
            version="1",
            chain_id=1,
            nonce="nonce-1",
            issued_at=issued_at,
        )

    repo.get = fail_unlocked_lookup  # type: ignore[method-assign]
    repo.get_for_update = locked_lookup  # type: ignore[method-assign]
    monkeypatch.setattr(wallet_change_module, "verify_siwe_signature", fake_verify)

    account_after, _ = await service.confirm_change(
        ActorContext(account_id=1),
        message="ignored",
        signature="ignored",
    )

    assert account_after.id == account.id
    assert locked_calls == 1
