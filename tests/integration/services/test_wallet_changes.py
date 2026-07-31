import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from eth_account import Account as EthAccount
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import create_account

from app.core.actor import ActorContext
from app.core.config import Settings
from app.core.errors import ConflictError, InvalidStateError, PermissionDeniedError
from app.core.security import AuthTokenType, decode_jwt
from app.db.models import Account, WalletChangeLog
from app.services.wallet_changes import confirm_wallet_change, initiate_wallet_change

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("migrated_database"),
]


def _wallet_change_settings() -> Settings:
    return Settings(
        jwt_secret_key="test-secret-key-with-32-bytes-123",
        siwe_domain="testserver",
        siwe_nonce_expiry=300,
        wallet_change_cooldown=604800,
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


async def _get_account(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
) -> Account:
    async with db_session_factory() as session:
        account = await session.get(Account, account_id)
    assert account is not None
    return account


def _jwt_actor(account_id: int) -> ActorContext:
    return ActorContext(account_id=account_id, auth_method="jwt")


async def _initiate_and_sign(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    account_id: int,
    new_signer: LocalAccount,
) -> tuple[str, str]:
    async with db_session_factory() as session:
        challenge = await initiate_wallet_change(
            session=session,
            settings=settings,
            actor=_jwt_actor(account_id),
            wallet_address=new_signer.address,
        )
    issued_at = datetime.now(UTC).replace(microsecond=0)
    message = _build_siwe_message(
        address=new_signer.address,
        nonce=challenge.nonce,
        issued_at=issued_at,
    )
    signature = _sign_message(new_signer, message)
    return message, signature


async def test_initiate_rejects_current_wallet(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    wallet_address = EthAccount.create().address
    account_id = await create_account(db_session_factory, wallet_address=wallet_address)

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="new wallet must differ"):
            await initiate_wallet_change(
                session=session,
                settings=settings,
                actor=_jwt_actor(account_id),
                wallet_address=wallet_address,
            )


async def test_initiate_rejects_wallet_already_used(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    account_id = await create_account(db_session_factory)
    other_wallet_address = EthAccount.create().address
    await create_account(db_session_factory, wallet_address=other_wallet_address)

    async with db_session_factory() as session:
        with pytest.raises(ConflictError, match="already in use"):
            await initiate_wallet_change(
                session=session,
                settings=settings,
                actor=_jwt_actor(account_id),
                wallet_address=other_wallet_address,
            )


async def test_initiate_rejects_during_cooldown(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    account_id = await create_account(db_session_factory)

    async with db_session_factory.begin() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.wallet_changed_at = datetime.now(UTC) - timedelta(hours=1)

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="cooldown"):
            await initiate_wallet_change(
                session=session,
                settings=settings,
                actor=_jwt_actor(account_id),
                wallet_address=EthAccount.create().address,
            )


async def test_initiate_success_returns_challenge_and_rotates_nonce(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    account_id = await create_account(db_session_factory)
    before = await _get_account(db_session_factory, account_id)
    new_wallet_address = EthAccount.create().address

    async with db_session_factory() as session:
        challenge = await initiate_wallet_change(
            session=session,
            settings=settings,
            actor=_jwt_actor(account_id),
            wallet_address=new_wallet_address,
        )

    assert challenge.nonce

    after = await _get_account(db_session_factory, account_id)
    assert after.nonce == challenge.nonce
    assert after.nonce != before.nonce
    assert after.updated_at > before.updated_at
    assert challenge.expires_at == after.nonce_issued_at + timedelta(
        seconds=settings.siwe_nonce_expiry,
    )


async def test_initiate_requires_jwt_auth_method(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    account_id = await create_account(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(PermissionDeniedError, match="jwt authentication"):
            await initiate_wallet_change(
                session=session,
                settings=settings,
                actor=ActorContext(account_id=account_id, auth_method="api_key"),
                wallet_address=EthAccount.create().address,
            )


async def test_confirm_expired_nonce_raises_invalid_state(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    account_id = await create_account(db_session_factory)
    new_signer = EthAccount.create()
    message, signature = await _initiate_and_sign(
        db_session_factory,
        settings=settings,
        account_id=account_id,
        new_signer=new_signer,
    )

    async with db_session_factory.begin() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.nonce_issued_at = datetime.now(UTC) - timedelta(
            seconds=settings.siwe_nonce_expiry + 60,
        )

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError, match="expired"):
            await confirm_wallet_change(
                session=session,
                settings=settings,
                actor=_jwt_actor(account_id),
                message=message,
                signature=signature,
            )


async def test_confirm_bad_signature_raises_invalid_state(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    account_id = await create_account(db_session_factory)
    new_signer = EthAccount.create()
    message, _ = await _initiate_and_sign(
        db_session_factory,
        settings=settings,
        account_id=account_id,
        new_signer=new_signer,
    )
    wrong_signer = EthAccount.create()
    bad_signature = _sign_message(wrong_signer, message)

    async with db_session_factory() as session:
        with pytest.raises(InvalidStateError):
            await confirm_wallet_change(
                session=session,
                settings=settings,
                actor=_jwt_actor(account_id),
                message=message,
                signature=bad_signature,
            )


async def test_confirm_wallet_taken_after_initiate_raises_conflict(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    account_id = await create_account(db_session_factory)
    new_signer = EthAccount.create()
    message, signature = await _initiate_and_sign(
        db_session_factory,
        settings=settings,
        account_id=account_id,
        new_signer=new_signer,
    )

    await create_account(db_session_factory, wallet_address=new_signer.address)

    async with db_session_factory() as session:
        with pytest.raises(ConflictError, match="already in use"):
            await confirm_wallet_change(
                session=session,
                settings=settings,
                actor=_jwt_actor(account_id),
                message=message,
                signature=signature,
            )


async def test_confirm_success_updates_account_and_issues_tokens(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    account_id = await create_account(db_session_factory)
    before = await _get_account(db_session_factory, account_id)
    new_signer = EthAccount.create()

    async with db_session_factory() as session:
        challenge = await initiate_wallet_change(
            session=session,
            settings=settings,
            actor=_jwt_actor(account_id),
            wallet_address=new_signer.address,
        )
    issued_at = datetime.now(UTC).replace(microsecond=0)
    message = _build_siwe_message(
        address=new_signer.address,
        nonce=challenge.nonce,
        issued_at=issued_at,
    )
    signature = _sign_message(new_signer, message)

    async with db_session_factory() as session:
        account, tokens = await confirm_wallet_change(
            session=session,
            settings=settings,
            actor=_jwt_actor(account_id),
            message=message,
            signature=signature,
        )

    assert account.wallet_address == new_signer.address
    assert tokens.access_token
    assert tokens.refresh_token

    claims = decode_jwt(
        tokens.access_token,
        secret_key=settings.jwt_secret_key,
        expected_token_type=AuthTokenType.ACCESS,
    )
    assert claims.wallet_address == new_signer.address

    after = await _get_account(db_session_factory, account_id)
    assert after.wallet_address == new_signer.address
    assert after.wallet_changed_at is not None
    assert after.updated_at > before.updated_at
    assert after.token_version == before.token_version + 1
    assert after.nonce != challenge.nonce

    async with db_session_factory() as session:
        logs = list(
            await session.scalars(
                select(WalletChangeLog).where(WalletChangeLog.account_id == account_id),
            ),
        )
    assert len(logs) == 1
    assert logs[0].previous_wallet_address == before.wallet_address
    assert logs[0].new_wallet_address == new_signer.address


async def test_confirm_concurrent_same_target_wallet_only_one_succeeds(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _wallet_change_settings()
    account_a_id = await create_account(db_session_factory)
    account_b_id = await create_account(db_session_factory)
    new_signer = EthAccount.create()

    message_a, signature_a = await _initiate_and_sign(
        db_session_factory,
        settings=settings,
        account_id=account_a_id,
        new_signer=new_signer,
    )
    message_b, signature_b = await _initiate_and_sign(
        db_session_factory,
        settings=settings,
        account_id=account_b_id,
        new_signer=new_signer,
    )

    async def _confirm(account_id: int, message: str, signature: str) -> None:
        async with db_session_factory() as session:
            await confirm_wallet_change(
                session=session,
                settings=settings,
                actor=_jwt_actor(account_id),
                message=message,
                signature=signature,
            )

    results = await asyncio.gather(
        _confirm(account_a_id, message_a, signature_a),
        _confirm(account_b_id, message_b, signature_b),
        return_exceptions=True,
    )

    successes = [result for result in results if result is None]
    failures = [result for result in results if isinstance(result, ConflictError)]
    assert len(successes) == 1
    assert len(failures) == 1

    async with db_session_factory() as session:
        holders = list(
            await session.scalars(
                select(Account).where(Account.wallet_address == new_signer.address),
            ),
        )
        logs = list(
            await session.scalars(
                select(WalletChangeLog).where(
                    WalletChangeLog.new_wallet_address == new_signer.address,
                ),
            ),
        )
    assert len(holders) == 1
    assert len(logs) == 1
