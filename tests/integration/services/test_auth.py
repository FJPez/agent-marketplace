import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from eth_account import Account as EthAccount
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import create_account

from app.core.config import Settings
from app.core.errors import PermissionDeniedError, UnauthenticatedError
from app.core.security import AuthTokenType, create_jwt, hash_api_key
from app.db.models import Account, ApiKey
from app.services.auth import (
    issue_nonce,
    refresh_access_token,
    resolve_actor,
    resolve_jwt_actor,
    verify_wallet,
)


def _auth_settings() -> Settings:
    return Settings(
        jwt_secret_key="test-secret-key-with-32-bytes-123",
        siwe_domain="testserver",
        siwe_nonce_expiry=300,
    )


def _build_siwe_message(*, address: str, nonce: str, issued_at: datetime) -> str:
    return "\n".join(
        [
            "testserver wants you to sign in with your Ethereum account:",
            address,
            "",
            "URI: http://testserver",
            "Version: 1",
            "Chain ID: 1",
            f"Nonce: {nonce}",
            f"Issued At: {issued_at.isoformat().replace('+00:00', 'Z')}",
        ],
    )


def _sign_message(signer: LocalAccount, message: str) -> str:
    signed = EthAccount.sign_message(
        signable_message=encode_defunct(text=message),
        private_key=signer.key,
    )
    return signed.signature.to_0x_hex()


async def _get_account_by_wallet(
    db_session_factory: async_sessionmaker[AsyncSession],
    wallet_address: str,
) -> Account | None:
    async with db_session_factory() as session:
        return await session.scalar(
            select(Account).where(Account.wallet_address == wallet_address),
        )


@pytest.mark.asyncio
async def test_issue_nonce_creates_account_and_reuses_active_nonce(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()

    async with db_session_factory() as session:
        first_nonce = await issue_nonce(
            session=session,
            settings=settings,
            wallet_address=signer.address,
        )

    account = await _get_account_by_wallet(db_session_factory, signer.address)
    assert account is not None
    assert account.nonce == first_nonce

    async with db_session_factory() as session:
        second_nonce = await issue_nonce(
            session=session,
            settings=settings,
            wallet_address=signer.address,
        )
    assert second_nonce == first_nonce


@pytest.mark.asyncio
async def test_issue_nonce_issues_new_nonce_after_expiry(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()
    stale_issued_at = datetime.now(UTC) - timedelta(seconds=settings.siwe_nonce_expiry + 60)

    async with db_session_factory.begin() as session:
        session.add(
            Account(
                wallet_address=signer.address,
                display_name="Anonymous",
                nonce="stale-nonce",
                nonce_issued_at=stale_issued_at,
            ),
        )

    async with db_session_factory() as session:
        new_nonce = await issue_nonce(
            session=session,
            settings=settings,
            wallet_address=signer.address,
        )

    assert new_nonce != "stale-nonce"
    account = await _get_account_by_wallet(db_session_factory, signer.address)
    assert account is not None
    assert account.nonce == new_nonce


@pytest.mark.asyncio
async def test_issue_nonce_concurrent_calls_create_single_account(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()

    async def _call() -> str:
        async with db_session_factory() as session:
            return await issue_nonce(
                session=session,
                settings=settings,
                wallet_address=signer.address,
            )

    first_nonce, second_nonce = await asyncio.gather(_call(), _call())

    assert first_nonce
    assert second_nonce

    async with db_session_factory() as session:
        rows = list(
            await session.scalars(
                select(Account).where(Account.wallet_address == signer.address),
            ),
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_verify_wallet_success_returns_tokens_and_rotates_nonce(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()

    async with db_session_factory() as session:
        nonce = await issue_nonce(
            session=session,
            settings=settings,
            wallet_address=signer.address,
        )

    issued_at = datetime.now(UTC).replace(microsecond=0)
    message = _build_siwe_message(address=signer.address, nonce=nonce, issued_at=issued_at)
    signature = _sign_message(signer, message)

    async with db_session_factory() as session:
        result = await verify_wallet(
            session=session,
            settings=settings,
            message=message,
            signature=signature,
        )

    assert result.access_token
    assert result.refresh_token
    assert result.account.wallet_address == signer.address

    account = await _get_account_by_wallet(db_session_factory, signer.address)
    assert account is not None
    assert account.nonce != nonce


@pytest.mark.asyncio
async def test_verify_wallet_wrong_nonce_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()

    async with db_session_factory() as session:
        await issue_nonce(session=session, settings=settings, wallet_address=signer.address)

    issued_at = datetime.now(UTC).replace(microsecond=0)
    message = _build_siwe_message(
        address=signer.address,
        nonce="not-the-real-nonce",
        issued_at=issued_at,
    )
    signature = _sign_message(signer, message)

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError, match="nonce is not valid"):
            await verify_wallet(
                session=session,
                settings=settings,
                message=message,
                signature=signature,
            )


@pytest.mark.asyncio
async def test_verify_wallet_expired_account_nonce_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()

    async with db_session_factory() as session:
        nonce = await issue_nonce(
            session=session,
            settings=settings,
            wallet_address=signer.address,
        )

    stale_issued_at = datetime.now(UTC) - timedelta(seconds=settings.siwe_nonce_expiry + 60)
    async with db_session_factory.begin() as session:
        account = await session.scalar(
            select(Account).where(Account.wallet_address == signer.address),
        )
        assert account is not None
        account.nonce_issued_at = stale_issued_at

    message = _build_siwe_message(
        address=signer.address,
        nonce=nonce,
        issued_at=datetime.now(UTC).replace(microsecond=0),
    )
    signature = _sign_message(signer, message)

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError, match="SIWE message has expired"):
            await verify_wallet(
                session=session,
                settings=settings,
                message=message,
                signature=signature,
            )


async def _verified_tokens(
    db_session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    signer: LocalAccount,
) -> tuple[str, str]:
    async with db_session_factory() as session:
        nonce = await issue_nonce(
            session=session,
            settings=settings,
            wallet_address=signer.address,
        )
    issued_at = datetime.now(UTC).replace(microsecond=0)
    message = _build_siwe_message(address=signer.address, nonce=nonce, issued_at=issued_at)
    signature = _sign_message(signer, message)
    async with db_session_factory() as session:
        result = await verify_wallet(
            session=session,
            settings=settings,
            message=message,
            signature=signature,
        )
    return result.access_token, result.refresh_token


@pytest.mark.asyncio
async def test_refresh_access_token_returns_new_access_token(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()
    _, refresh_token = await _verified_tokens(db_session_factory, settings, signer)

    async with db_session_factory() as session:
        access_token = await refresh_access_token(
            session=session,
            settings=settings,
            refresh_token=refresh_token,
        )

    assert access_token


@pytest.mark.asyncio
async def test_refresh_access_token_garbage_token_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError):
            await refresh_access_token(
                session=session,
                settings=settings,
                refresh_token="garbage",
            )


@pytest.mark.asyncio
async def test_refresh_access_token_deleted_account_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()
    _, refresh_token = await _verified_tokens(db_session_factory, settings, signer)

    async with db_session_factory.begin() as session:
        account = await session.scalar(
            select(Account).where(Account.wallet_address == signer.address),
        )
        assert account is not None
        await session.delete(account)

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError):
            await refresh_access_token(
                session=session,
                settings=settings,
                refresh_token=refresh_token,
            )


@pytest.mark.asyncio
async def test_refresh_access_token_stale_token_version_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()
    _, refresh_token = await _verified_tokens(db_session_factory, settings, signer)

    async with db_session_factory.begin() as session:
        account = await session.scalar(
            select(Account).where(Account.wallet_address == signer.address),
        )
        assert account is not None
        account.token_version += 1

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError):
            await refresh_access_token(
                session=session,
                settings=settings,
                refresh_token=refresh_token,
            )


@pytest.mark.asyncio
async def test_resolve_actor_jwt_path_returns_actor_context(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    signer = EthAccount.create()
    access_token, _ = await _verified_tokens(db_session_factory, settings, signer)
    account = await _get_account_by_wallet(db_session_factory, signer.address)
    assert account is not None

    async with db_session_factory() as session:
        actor = await resolve_actor(
            session=session,
            settings=settings,
            authorization=f"Bearer {access_token}",
        )

    assert actor.account_id == account.id
    assert actor.is_admin == account.is_admin
    assert actor.wallet_address == account.wallet_address
    assert actor.auth_method == "jwt"


@pytest.mark.asyncio
async def test_resolve_actor_jwt_path_garbage_token_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError):
            await resolve_actor(
                session=session,
                settings=settings,
                authorization="Bearer garbage",
            )


@pytest.mark.asyncio
async def test_resolve_actor_jwt_path_unknown_account_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    token = create_jwt(
        secret_key=settings.jwt_secret_key,
        account_id=999_999,
        wallet_address="0x" + "1" * 40,
        token_version=1,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=settings.jwt_access_token_expiry,
    )

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError):
            await resolve_actor(
                session=session,
                settings=settings,
                authorization=f"Bearer {token}",
            )


@pytest.mark.asyncio
async def test_resolve_actor_jwt_path_stale_token_version_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    account_id = await create_account(db_session_factory, display_name="Alpha")
    async with db_session_factory() as session:
        persisted = await session.get(Account, account_id)
        assert persisted is not None
        wallet_address = persisted.wallet_address
    assert wallet_address is not None

    token = create_jwt(
        secret_key=settings.jwt_secret_key,
        account_id=account_id,
        wallet_address=wallet_address,
        token_version=1,
        token_type=AuthTokenType.ACCESS,
        expires_in_seconds=settings.jwt_access_token_expiry,
    )

    async with db_session_factory.begin() as session:
        persisted = await session.get(Account, account_id)
        assert persisted is not None
        persisted.token_version += 1

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError):
            await resolve_actor(
                session=session,
                settings=settings,
                authorization=f"Bearer {token}",
            )


async def _create_api_key(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    account_id: int,
    settings: Settings,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> str:
    raw_key = f"{settings.api_key_prefix}{account_id}-secret"
    async with db_session_factory.begin() as session:
        session.add(
            ApiKey(
                account_id=account_id,
                name="test key",
                key_prefix=raw_key[:16],
                key_hash=hash_api_key(raw_key),
                expires_at=expires_at,
                revoked_at=revoked_at,
            ),
        )
    return raw_key


@pytest.mark.asyncio
async def test_resolve_actor_api_key_path_returns_actor_and_touches_last_used(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    account_id = await create_account(db_session_factory, display_name="Alpha")
    raw_key = await _create_api_key(db_session_factory, account_id=account_id, settings=settings)

    async with db_session_factory() as session:
        actor = await resolve_actor(
            session=session,
            settings=settings,
            authorization=f"Bearer {raw_key}",
        )

    assert actor.account_id == account_id
    assert actor.auth_method == "api_key"

    async with db_session_factory() as session:
        api_key = await session.scalar(
            select(ApiKey).where(ApiKey.account_id == account_id),
        )
    assert api_key is not None
    assert api_key.last_used_at is not None


@pytest.mark.asyncio
async def test_resolve_actor_api_key_path_no_touch_leaves_last_used_none(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    account_id = await create_account(db_session_factory, display_name="Alpha")
    raw_key = await _create_api_key(db_session_factory, account_id=account_id, settings=settings)

    async with db_session_factory() as session:
        await resolve_actor(
            session=session,
            settings=settings,
            authorization=f"Bearer {raw_key}",
            touch_api_key=False,
        )

    async with db_session_factory() as session:
        api_key = await session.scalar(
            select(ApiKey).where(ApiKey.account_id == account_id),
        )
    assert api_key is not None
    assert api_key.last_used_at is None


@pytest.mark.asyncio
async def test_resolve_actor_api_key_path_revoked_key_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    account_id = await create_account(db_session_factory, display_name="Alpha")
    raw_key = await _create_api_key(
        db_session_factory,
        account_id=account_id,
        settings=settings,
        revoked_at=datetime.now(UTC),
    )

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError, match="invalid api key"):
            await resolve_actor(
                session=session,
                settings=settings,
                authorization=f"Bearer {raw_key}",
            )


@pytest.mark.asyncio
async def test_resolve_actor_api_key_path_expired_key_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    account_id = await create_account(db_session_factory, display_name="Alpha")
    raw_key = await _create_api_key(
        db_session_factory,
        account_id=account_id,
        settings=settings,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError, match="api key has expired"):
            await resolve_actor(
                session=session,
                settings=settings,
                authorization=f"Bearer {raw_key}",
            )


@pytest.mark.asyncio
async def test_resolve_actor_non_bearer_authorization_raises_unauthenticated(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()

    async with db_session_factory() as session:
        with pytest.raises(UnauthenticatedError):
            await resolve_actor(
                session=session,
                settings=settings,
                authorization="Token abc",
            )


@pytest.mark.asyncio
async def test_resolve_jwt_actor_with_api_key_raises_permission_denied(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database
    settings = _auth_settings()
    account_id = await create_account(db_session_factory, display_name="Alpha")
    raw_key = await _create_api_key(db_session_factory, account_id=account_id, settings=settings)

    async with db_session_factory() as session:
        with pytest.raises(PermissionDeniedError, match="jwt authentication required"):
            await resolve_jwt_actor(
                session=session,
                settings=settings,
                authorization=f"Bearer {raw_key}",
            )
