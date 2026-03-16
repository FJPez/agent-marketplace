from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.core.security import (
    AuthTokenType,
    create_jwt,
    generate_nonce,
    verify_siwe_signature,
)
from app.repositories.account_repo import AccountRepository
from app.repositories.wallet_change_log_repo import WalletChangeLogRepository
from app.services.auth_service import TokenPair

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.actor import ActorContext
    from app.core.config import Settings
    from app.db.models import Account


class WalletChangeError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WalletChangeChallenge:
    nonce: str
    expires_at: datetime


class WalletChangeService:
    def __init__(self, session: AsyncSession, *, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._account_repo = AccountRepository(session)
        self._wallet_change_log_repo = WalletChangeLogRepository(session)

    async def initiate_change(
        self,
        actor: ActorContext,
        *,
        wallet_address: str,
    ) -> WalletChangeChallenge:
        if actor.auth_method != "jwt":
            raise WalletChangeError("wallet change requires jwt authentication")
        account = await self._require_account(actor.account_id)
        if account.wallet_address == wallet_address:
            raise WalletChangeError("new wallet must differ from current wallet")
        self._ensure_cooldown(account)
        existing_account = await self._account_repo.get_by_wallet_address(wallet_address)
        if existing_account is not None:
            raise WalletChangeError("wallet address is already in use")

        nonce = generate_nonce()
        issued_at = datetime.now(UTC)
        self._account_repo.update_nonce(account, nonce=nonce, issued_at=issued_at)
        await self._session.commit()
        return WalletChangeChallenge(
            nonce=nonce,
            expires_at=issued_at + timedelta(seconds=self._settings.siwe_nonce_expiry),
        )

    async def confirm_change(
        self,
        actor: ActorContext,
        *,
        message: str,
        signature: str,
    ) -> tuple[Account, TokenPair]:
        if actor.auth_method != "jwt":
            raise WalletChangeError("wallet change requires jwt authentication")
        account = await self._require_account(actor.account_id)
        self._ensure_cooldown(account)
        expires_at = account.nonce_issued_at + timedelta(seconds=self._settings.siwe_nonce_expiry)
        if expires_at < datetime.now(UTC):
            raise WalletChangeError("SIWE message has expired")

        try:
            parsed = verify_siwe_signature(
                self._settings,
                message=message,
                signature=signature,
                expected_nonce=account.nonce,
                now=datetime.now(UTC),
            )
        except ValueError as exc:
            raise WalletChangeError(str(exc)) from exc

        if parsed.address == account.wallet_address:
            raise WalletChangeError("new wallet must differ from current wallet")
        existing_account = await self._account_repo.get_by_wallet_address(parsed.address)
        if existing_account is not None and existing_account.id != account.id:
            raise WalletChangeError("wallet address is already in use")

        self._wallet_change_log_repo.add(
            account_id=account.id,
            previous_wallet_address=account.wallet_address,
            new_wallet_address=parsed.address,
        )
        self._account_repo.update_wallet(
            account,
            wallet_address=parsed.address,
            wallet_changed_at=datetime.now(UTC),
        )
        self._account_repo.bump_token_version(account)
        self._account_repo.update_nonce(account, nonce=generate_nonce())
        await self._session.commit()
        await self._session.refresh(account)
        return account, self._issue_tokens(account)

    async def _require_account(self, account_id: int) -> Account:
        account = await self._account_repo.get(account_id)
        if account is None:
            raise WalletChangeError("account not found")
        return account

    def _ensure_cooldown(self, account: Account) -> None:
        if account.wallet_changed_at is None:
            return
        available_at = account.wallet_changed_at + timedelta(
            seconds=self._settings.wallet_change_cooldown,
        )
        if available_at > datetime.now(UTC):
            raise WalletChangeError("wallet change is in cooldown")

    def _issue_tokens(self, account: Account) -> TokenPair:
        access_token = create_jwt(
            secret_key=self._settings.jwt_secret_key,
            account_id=account.id,
            wallet_address=account.wallet_address,
            token_version=account.token_version,
            token_type=AuthTokenType.ACCESS,
            expires_in_seconds=self._settings.jwt_access_token_expiry,
        )
        refresh_token = create_jwt(
            secret_key=self._settings.jwt_secret_key,
            account_id=account.id,
            wallet_address=account.wallet_address,
            token_version=account.token_version,
            token_type=AuthTokenType.REFRESH,
            expires_in_seconds=self._settings.jwt_refresh_token_expiry,
        )
        return TokenPair(access_token=access_token, refresh_token=refresh_token)
