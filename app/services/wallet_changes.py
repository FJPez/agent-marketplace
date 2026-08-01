from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidInputError, InvalidStateError, NotFoundError
from app.core.security import generate_nonce, normalize_wallet_address, verify_siwe_signature
from app.db.errors import is_unique_violation
from app.db.models import Account, WalletChangeLog
from app.services.auth import TokenPair, issue_token_pair


@dataclass(frozen=True, slots=True)
class WalletChangeChallenge:
    nonce: str
    expires_at: datetime


async def initiate_wallet_change(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
    wallet_address: str,
) -> WalletChangeChallenge:
    try:
        normalized_wallet_address = normalize_wallet_address(wallet_address)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    now = datetime.now(UTC)
    account = await _require_account(session=session, account_id=account_id)
    if account.wallet_address == normalized_wallet_address:
        raise InvalidStateError("new wallet must differ from current wallet")
    _ensure_cooldown(account, settings=settings, now=now)
    existing_account = await session.scalar(
        select(Account).where(Account.wallet_address == normalized_wallet_address),
    )
    if existing_account is not None:
        raise ConflictError("wallet address is already in use")

    nonce = generate_nonce()
    account.nonce = nonce
    account.nonce_issued_at = now
    account.pending_wallet_address = normalized_wallet_address
    account.updated_at = now
    await session.commit()
    return WalletChangeChallenge(
        nonce=nonce,
        expires_at=now + timedelta(seconds=settings.siwe_nonce_expiry),
    )


async def confirm_wallet_change(
    *,
    session: AsyncSession,
    settings: Settings,
    account_id: int,
    message: str,
    signature: str,
) -> tuple[Account, TokenPair]:
    now = datetime.now(UTC)
    account = await _require_account(session=session, account_id=account_id)
    _ensure_cooldown(account, settings=settings, now=now)
    expires_at = account.nonce_issued_at + timedelta(seconds=settings.siwe_nonce_expiry)
    if expires_at < now:
        raise InvalidStateError("SIWE message has expired")

    try:
        parsed = verify_siwe_signature(
            settings,
            message=message,
            signature=signature,
            expected_nonce=account.nonce,
            now=now,
        )
    except ValueError as exc:
        raise InvalidStateError(str(exc)) from exc

    if account.pending_wallet_address is None:
        raise InvalidStateError("no wallet change is pending")
    if parsed.address != account.pending_wallet_address:
        raise InvalidStateError("signature does not match the pending wallet address")
    if parsed.address == account.wallet_address:
        raise InvalidStateError("new wallet must differ from current wallet")
    existing_account = await session.scalar(
        select(Account).where(Account.wallet_address == parsed.address),
    )
    if existing_account is not None and existing_account.id != account.id:
        raise ConflictError("wallet address is already in use")

    session.add(
        WalletChangeLog(
            account_id=account.id,
            previous_wallet_address=_require_wallet_address(account),
            new_wallet_address=parsed.address,
        ),
    )
    account.wallet_address = parsed.address
    account.wallet_changed_at = now
    account.pending_wallet_address = None
    account.token_version += 1
    account.nonce = generate_nonce()
    account.nonce_issued_at = now
    account.updated_at = now
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if not is_unique_violation(exc):
            raise
        raise ConflictError("wallet address is already in use") from exc
    await session.refresh(account)
    tokens = issue_token_pair(settings=settings, account=account)
    return account, tokens


async def _require_account(*, session: AsyncSession, account_id: int) -> Account:
    statement = select(Account).where(Account.id == account_id).with_for_update()
    account = await session.scalar(statement)
    if account is None:
        raise NotFoundError("account not found")
    return account


def _ensure_cooldown(account: Account, *, settings: Settings, now: datetime) -> None:
    if account.wallet_changed_at is None:
        return
    available_at = account.wallet_changed_at + timedelta(seconds=settings.wallet_change_cooldown)
    if available_at > now:
        raise InvalidStateError("wallet change is in cooldown")


def _require_wallet_address(account: Account) -> str:
    if account.wallet_address is None:
        msg = "account wallet address is required"
        raise InvalidStateError(msg)
    return account.wallet_address
