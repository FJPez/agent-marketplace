from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.errors import PermissionDeniedError, UnauthenticatedError
from app.core.security import (
    AuthTokenType,
    ParsedSiweMessage,
    create_jwt,
    decode_jwt,
    decode_token,
    generate_nonce,
    hash_api_key,
    normalize_wallet_address,
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
    wallet_address = _require_wallet_address(account)
    access_token = create_jwt(
        secret_key=settings.jwt_secret_key,
        account_id=account.id,
        wallet_address=wallet_address,
        token_version=account.token_version,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=settings.jwt_access_token_expiry,
    )
    refresh_token = create_jwt(
        secret_key=settings.jwt_secret_key,
        account_id=account.id,
        wallet_address=wallet_address,
        token_version=account.token_version,
        token_type=AuthTokenType.REFRESH,
        expires_in_seconds=settings.jwt_refresh_token_expiry,
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


async def issue_nonce(*, session: AsyncSession, settings: Settings, wallet_address: str) -> str:
    normalized_wallet = normalize_wallet_address(wallet_address)
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
    parsed = _parse_and_validate_message(settings=settings, message=message, signature=signature)
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
        payload = decode_token(
            settings,
            refresh_token,
            expected_type=AuthTokenType.REFRESH.value,
        )
    except InvalidTokenError as exc:
        raise UnauthenticatedError("invalid token") from exc
    account = await session.get(Account, int(payload.subject))
    if account is None:
        raise UnauthenticatedError("authenticated account does not exist")
    if account.token_version != payload.token_version:
        raise UnauthenticatedError("token version is not valid")
    return create_jwt(
        secret_key=settings.jwt_secret_key,
        account_id=account.id,
        wallet_address=_require_wallet_address(account),
        token_version=account.token_version,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=settings.jwt_access_token_expiry,
    )


async def resolve_actor(
    *,
    session: AsyncSession,
    settings: Settings,
    authorization: str,
    touch_api_key: bool = True,
) -> ActorContext:
    token = _extract_bearer_token(authorization)
    if token.startswith(settings.api_key_prefix):
        return await _resolve_api_key_actor(
            session=session,
            token=token,
            touch_api_key=touch_api_key,
        )
    return await _resolve_jwt_token_actor(session=session, settings=settings, token=token)


async def resolve_jwt_actor(
    *,
    session: AsyncSession,
    settings: Settings,
    authorization: str,
) -> ActorContext:
    actor = await resolve_actor(
        session=session,
        settings=settings,
        authorization=authorization,
        touch_api_key=False,
    )
    if actor.auth_method != "jwt":
        raise PermissionDeniedError("jwt authentication required")
    return actor


def _require_wallet_address(account: Account) -> str:
    if account.wallet_address is None:
        msg = "account wallet address is required"
        raise UnauthenticatedError(msg)
    return account.wallet_address


def _extract_bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or not token:
        msg = "Bearer token is required"
        raise UnauthenticatedError(msg)
    return token


def _parse_and_validate_message(
    *,
    settings: Settings,
    message: str,
    signature: str,
) -> ParsedSiweMessage:
    try:
        parsed = verify_siwe_signature(
            settings,
            message=message,
            signature=signature,
            expected_nonce=_extract_nonce(message),
            now=datetime.now(UTC),
        )
    except ValueError as exc:
        raise UnauthenticatedError(str(exc)) from exc
    return parsed


def _extract_nonce(message: str) -> str:
    for line in message.splitlines():
        if line.startswith("Nonce: "):
            return line.removeprefix("Nonce: ")
    raise UnauthenticatedError("nonce is not valid")


def _nonce_is_active(account: Account, *, settings: Settings) -> bool:
    if not account.nonce:
        return False
    expires_at = account.nonce_issued_at + timedelta(seconds=settings.siwe_nonce_expiry)
    return expires_at >= datetime.now(UTC)


async def _resolve_api_key_actor(
    *,
    session: AsyncSession,
    token: str,
    touch_api_key: bool,
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

    if touch_api_key:
        api_key.last_used_at = now
        await session.commit()
    return ActorContext(
        account_id=account.id,
        is_admin=account.is_admin,
        account_type=account.account_type,
        auth_method="api_key",
        wallet_address=_require_wallet_address(account),
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
            secret_key=settings.jwt_secret_key,
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
        wallet_address=_require_wallet_address(account),
    )
