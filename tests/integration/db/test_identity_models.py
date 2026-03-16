from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account


@pytest.mark.asyncio
async def test_account_model_persists_unified_identity_fields(
    migrated_database: None,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _ = migrated_database

    async with db_session_factory.begin() as session:
        account = Account(
            wallet_address="0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6",
            account_type="human",
            is_admin=True,
            display_name="Readable Account",
            nonce="nonce-1",
            nonce_issued_at=datetime(2026, 3, 16, 12, 0, tzinfo=UTC),
            token_version=4,
        )
        session.add(account)
        await session.flush()
        account_id = account.id

    async with db_session_factory() as session:
        persisted_account = await session.get(Account, account_id)

    assert persisted_account is not None
    assert persisted_account.wallet_address == "0x742d35Cc6634C0532925A3B8D4C9dB96C4B4d8B6"
    assert persisted_account.account_type == "human"
    assert persisted_account.is_admin is True
    assert persisted_account.display_name == "Readable Account"
    assert persisted_account.nonce == "nonce-1"
    assert persisted_account.nonce_issued_at == datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
    assert persisted_account.token_version == 4
    assert persisted_account.created_at is not None
    assert persisted_account.updated_at is not None
