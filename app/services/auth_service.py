from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from jwt import InvalidTokenError

from app.core.security import (
    AuthTokenType,
    ParsedSiweMessage,
    create_jwt,
    decode_token,
    generate_nonce,
    normalize_wallet_address,
    verify_siwe_signature,
)
from app.repositories.account_repo import AccountRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.db.models import Account


class AuthValidationError(Exception):
    pass


class AuthenticationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str


@dataclass(frozen=True, slots=True)
class AuthResult:
    account: Account
    access_token: str
    refresh_token: str


class AuthService:
    def __init__(self, session: AsyncSession, *, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._account_repo = AccountRepository(session)

    async def issue_nonce(self, *, wallet_address: str) -> str:
        normalized_wallet = normalize_wallet_address(wallet_address)
        account = await self._account_repo.get_by_wallet_address(normalized_wallet)
        nonce = generate_nonce()
        if account is None:
            await self._account_repo.create(
                wallet_address=normalized_wallet,
                display_name="Anonymous",
                nonce=nonce,
                nonce_issued_at=datetime.now(UTC),
            )
        else:
            self._account_repo.update_nonce(account, nonce=nonce)
        await self._session.commit()
        return nonce

    async def verify_wallet(self, *, message: str, signature: str) -> AuthResult:
        parsed = self._parse_and_validate_message(message=message, signature=signature)
        account = await self._account_repo.get_by_wallet_address(parsed.address)
        if account is None:
            raise AuthenticationError("account not found for wallet")
        if account.nonce != parsed.nonce:
            raise AuthenticationError("nonce is not valid")
        expires_at = account.nonce_issued_at + timedelta(seconds=self._settings.siwe_nonce_expiry)
        if expires_at < datetime.now(UTC):
            raise AuthenticationError("SIWE message has expired")

        self._account_repo.update_nonce(account, nonce=generate_nonce())
        await self._session.commit()
        await self._session.refresh(account)
        tokens = self._build_token_pair(account)
        return AuthResult(
            account=account,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
        )

    async def refresh_access_token(self, *, refresh_token: str) -> str:
        try:
            payload = decode_token(
                self._settings,
                refresh_token,
                expected_type=AuthTokenType.REFRESH.value,
            )
        except InvalidTokenError as exc:
            raise AuthenticationError("invalid token") from exc
        account = await self._account_repo.get(int(payload.subject))
        if account is None:
            raise AuthenticationError("authenticated account does not exist")
        if account.token_version != payload.token_version:
            raise AuthenticationError("token version is not valid")
        return create_jwt(
            secret_key=self._settings.jwt_secret_key,
            account_id=account.id,
            wallet_address=account.wallet_address,
            token_version=account.token_version,
            token_type=AuthTokenType.ACCESS,
            expires_in_seconds=self._settings.jwt_access_token_expiry,
        )

    def _parse_and_validate_message(
        self,
        *,
        message: str,
        signature: str,
    ) -> ParsedSiweMessage:
        try:
            parsed = verify_siwe_signature(
                self._settings,
                message=message,
                signature=signature,
                expected_nonce=self._extract_nonce(message),
                now=datetime.now(UTC),
            )
        except ValueError as exc:
            raise AuthenticationError(str(exc)) from exc
        return parsed

    def _extract_nonce(self, message: str) -> str:
        for line in message.splitlines():
            if line.startswith("Nonce: "):
                return line.removeprefix("Nonce: ")
        raise AuthenticationError("nonce is not valid")

    def _build_token_pair(self, account: Account) -> TokenPair:
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
