from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jwt import InvalidTokenError
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.errors import UnauthenticatedError
from app.core.evm import checksum_address
from app.core.security import (
    AuthTokenType,
    create_jwt,
    decode_jwt,
    generate_nonce,
    hash_api_key,
    verify_siwe_signature,
)
from app.db.models import Account, ApiKey


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str


@dataclass(frozen=True, slots=True)
class AuthResult:
    account: Account
    access_token: str
    refresh_token: str


def issue_token_pair(*, settings: Settings, account: Account) -> TokenPair:
    access_token = create_jwt(
        secret_key=settings.jwt_secret_key.get_secret_value(),
        account_id=account.id,
        wallet_address=account.wallet_address,
        token_version=account.token_version,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=settings.jwt_access_token_expiry,
    )
    refresh_token = create_jwt(
        secret_key=settings.jwt_secret_key.get_secret_value(),
        account_id=account.id,
        wallet_address=account.wallet_address,
        token_version=account.token_version,
        token_type=AuthTokenType.REFRESH,
        expires_in_seconds=settings.jwt_refresh_token_expiry,
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


async def issue_nonce(*, session: AsyncSession, settings: Settings, wallet_address: str) -> str:
    normalized_wallet = checksum_address(wallet_address)
    account = await session.scalar(
        select(Account).where(Account.wallet_address == normalized_wallet),
    )
    if account is not None and _nonce_is_active(account, settings=settings):
        return account.nonce

    nonce = generate_nonce()
    issued_at = datetime.now(UTC)
    if account is None:
        try:
            new_account = Account(
                wallet_address=normalized_wallet,
                display_name="Anonymous",
                nonce=nonce,
                nonce_issued_at=issued_at,
            )
            session.add(new_account)
            await session.flush()
        except IntegrityError:
            await session.rollback()
            account = await session.scalar(
                select(Account).where(Account.wallet_address == normalized_wallet),
            )
            if account is None:
                raise
            if _nonce_is_active(account, settings=settings):
                return account.nonce
            account.nonce = nonce
            account.nonce_issued_at = issued_at
            account.updated_at = issued_at
    else:
        account.nonce = nonce
        account.nonce_issued_at = issued_at
        account.updated_at = issued_at
    await session.commit()
    return nonce


async def verify_wallet(
    *,
    session: AsyncSession,
    settings: Settings,
    message: str,
    signature: str,
) -> AuthResult:
    try:
        parsed = verify_siwe_signature(settings, message=message, signature=signature)
    except ValueError as exc:
        raise UnauthenticatedError(str(exc)) from exc
    account = await session.scalar(
        select(Account).where(Account.wallet_address == parsed.address).with_for_update(),
    )
    if account is None:
        raise UnauthenticatedError("account not found for wallet")
    if account.nonce != parsed.nonce:
        raise UnauthenticatedError("nonce is not valid")
    now = datetime.now(UTC)
    expires_at = account.nonce_issued_at + timedelta(seconds=settings.siwe_nonce_expiry)
    if expires_at < now:
        raise UnauthenticatedError("SIWE message has expired")

    account.nonce = generate_nonce()
    account.nonce_issued_at = now
    account.updated_at = now
    await session.commit()
    await session.refresh(account)
    tokens = issue_token_pair(settings=settings, account=account)
    return AuthResult(
        account=account,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
    )


async def refresh_access_token(
    *,
    session: AsyncSession,
    settings: Settings,
    refresh_token: str,
) -> str:
    try:
        claims = decode_jwt(
            refresh_token,
            secret_key=settings.jwt_secret_key.get_secret_value(),
            expected_token_type=AuthTokenType.REFRESH,
        )
    except InvalidTokenError as exc:
        raise UnauthenticatedError("invalid token") from exc
    account = await session.get(Account, claims.account_id)
    if account is None:
        raise UnauthenticatedError("authenticated account does not exist")
    if account.token_version != claims.token_version:
        raise UnauthenticatedError("token version is not valid")
    return create_jwt(
        secret_key=settings.jwt_secret_key.get_secret_value(),
        account_id=account.id,
        wallet_address=account.wallet_address,
        token_version=account.token_version,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=settings.jwt_access_token_expiry,
    )


async def resolve_actor(
    *,
    session: AsyncSession,
    settings: Settings,
    token: str,
) -> ActorContext:
    """Resolve a bearer token, an API key or a JWT access token, to its actor."""
    if token.startswith(settings.api_key_prefix):
        actor = await _resolve_api_key_actor(session=session, settings=settings, token=token)
        # Stores the key's last use when `_touch_api_key` recorded one.
        await session.commit()
        return actor
    return await _resolve_jwt_token_actor(session=session, settings=settings, token=token)


def _nonce_is_active(account: Account, *, settings: Settings) -> bool:
    if not account.nonce:
        return False
    expires_at = account.nonce_issued_at + timedelta(seconds=settings.siwe_nonce_expiry)
    return expires_at >= datetime.now(UTC)


async def _resolve_api_key_actor(
    *,
    session: AsyncSession,
    settings: Settings,
    token: str,
) -> ActorContext:
    api_key = await session.scalar(
        select(ApiKey).where(ApiKey.key_hash == hash_api_key(token)),
    )
    if api_key is None or api_key.revoked_at is not None:
        msg = "invalid api key"
        raise UnauthenticatedError(msg)
    now = datetime.now(UTC)
    if api_key.expires_at is not None and api_key.expires_at <= now:
        msg = "api key has expired"
        raise UnauthenticatedError(msg)

    account = await session.get(Account, api_key.account_id)
    if account is None:
        msg = "authenticated account does not exist"
        raise UnauthenticatedError(msg)

    actor = ActorContext(
        account_id=account.id,
        is_admin=account.is_admin,
        account_type=account.account_type,
        auth_method="api_key",
        wallet_address=account.wallet_address,
    )
    await _touch_api_key(session=session, settings=settings, api_key=api_key, now=now)
    return actor


async def _touch_api_key(
    *,
    session: AsyncSession,
    settings: Settings,
    api_key: ApiKey,
    now: datetime,
) -> None:
    """Record that the key was used, writing at most once per touch interval."""
    stale_before = now - timedelta(seconds=settings.api_key_touch_interval)
    if api_key.last_used_at is not None and api_key.last_used_at > stale_before:
        return
    # Checked again in SQL: a concurrent request may have touched the key since it was read.
    await session.execute(
        update(ApiKey)
        .where(
            ApiKey.id == api_key.id,
            or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at <= stale_before),
        )
        .values(last_used_at=now),
    )


async def _resolve_jwt_token_actor(
    *,
    session: AsyncSession,
    settings: Settings,
    token: str,
) -> ActorContext:
    try:
        claims = decode_jwt(
            token,
            secret_key=settings.jwt_secret_key.get_secret_value(),
            expected_token_type=AuthTokenType.ACCESS,
        )
    except InvalidTokenError as exc:
        msg = "invalid access token"
        raise UnauthenticatedError(msg) from exc

    account = await session.get(Account, claims.account_id)
    if account is None:
        msg = "authenticated account does not exist"
        raise UnauthenticatedError(msg)

    if account.token_version != claims.token_version:
        msg = "access token is no longer valid"
        raise UnauthenticatedError(msg)

    return ActorContext(
        account_id=account.id,
        is_admin=account.is_admin,
        account_type=account.account_type,
        auth_method="jwt",
        wallet_address=account.wallet_address,
    )
