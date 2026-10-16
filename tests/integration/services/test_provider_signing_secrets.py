import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.fixtures.domain import create_provider_account_record, create_signing_secret_record
from tests.fixtures.settings import TEST_PROVIDER_SECRET_ENCRYPTION_KEY

from app.core.errors import NotFoundError
from app.db.models import ProviderSigningSecret
from app.services import provider_signing_secrets


async def _stored_secret(
    db_session_factory: async_sessionmaker[AsyncSession],
    account_id: int,
) -> str:
    async with db_session_factory() as session:
        stored = await session.get(ProviderSigningSecret, account_id)
    assert stored is not None
    return Fernet(TEST_PROVIDER_SECRET_ENCRYPTION_KEY).decrypt(stored.ciphertext).decode()


async def test_issuing_stores_the_secret_encrypted_and_returns_it(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    secret = await create_signing_secret_record(db_session_factory, account_id=account_id)

    assert secret.startswith("amp_sig_")
    assert await _stored_secret(db_session_factory, account_id) == secret


async def test_issuing_again_replaces_the_stored_secret(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)
    first = await create_signing_secret_record(db_session_factory, account_id=account_id)

    second = await create_signing_secret_record(db_session_factory, account_id=account_id)

    assert second != first
    assert await _stored_secret(db_session_factory, account_id) == second


async def test_the_status_of_an_account_without_a_secret_is_not_found(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    account_id = await create_provider_account_record(db_session_factory)

    async with db_session_factory() as session:
        with pytest.raises(NotFoundError, match="the account has no signing secret"):
            await provider_signing_secrets.get_signing_secret(
                session=session,
                account_id=account_id,
            )
