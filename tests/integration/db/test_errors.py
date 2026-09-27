from collections.abc import Callable

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.helpers.auth import create_account

from app.db.errors import unique_violation_constraint
from app.db.models import Account, ApiKey

WALLET = "0x" + "a" * 40
TAKEN_KEY_HASH = "a" * 64


@pytest.mark.parametrize(
    ("conflicting_row", "expected"),
    [
        pytest.param(
            lambda _: Account(wallet_address=WALLET, display_name="Twin"),
            "ix_accounts_wallet_address",
            id="unique_index",
        ),
        pytest.param(
            lambda account_id: ApiKey(
                account_id=account_id,
                key_prefix="amp_",
                key_hash=TAKEN_KEY_HASH,
            ),
            "uq_api_keys_key_hash",
            id="unique_constraint",
        ),
        pytest.param(
            lambda account_id: ApiKey(
                account_id=account_id + 1,
                key_prefix="amp_",
                key_hash="b" * 64,
            ),
            None,
            id="foreign_key_violation",
        ),
    ],
)
async def test_unique_violation_constraint_names_the_violated_unique_key(
    db_session_factory: async_sessionmaker[AsyncSession],
    conflicting_row: Callable[[int], object],
    expected: str | None,
) -> None:
    account_id = await create_account(db_session_factory, wallet_address=WALLET)
    async with db_session_factory.begin() as session:
        session.add(ApiKey(account_id=account_id, key_prefix="amp_", key_hash=TAKEN_KEY_HASH))

    async with db_session_factory() as session:
        session.add(conflicting_row(account_id))
        with pytest.raises(IntegrityError) as caught:
            await session.flush()

    assert unique_violation_constraint(caught.value) == expected
