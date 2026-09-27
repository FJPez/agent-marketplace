from decimal import Decimal

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, text
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.types import AtomicAmount

# No model uses the type yet, so the tests store amounts in a temporary table
# that disappears with the test's connection.
amounts = Table(
    "atomic_amounts",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("amount", AtomicAmount()),
    prefixes=["TEMPORARY"],
)


async def _store(connection: AsyncConnection, value: object) -> None:
    await connection.run_sync(amounts.metadata.create_all)
    await connection.execute(insert(amounts).values(amount=value))


@pytest.mark.parametrize(
    "value",
    [0, 2**53 + 1, 2**256 - 1, 10**78 - 1, -(10**78 - 1)],
    ids=["zero", "beyond_float_precision", "max_uint256", "max_column", "min_column"],
)
async def test_atomic_amount_round_trips_exact_ints(db_engine: AsyncEngine, value: int) -> None:
    async with db_engine.connect() as connection:
        await _store(connection, value)
        stored = await connection.scalar(select(amounts.c.amount))

    assert type(stored) is int
    assert stored == value


@pytest.mark.parametrize("value", [True, 1.0, Decimal(1)], ids=["bool", "float", "decimal"])
async def test_atomic_amount_rejects_values_that_are_not_ints(
    db_engine: AsyncEngine,
    value: object,
) -> None:
    async with db_engine.connect() as connection:
        with pytest.raises(StatementError, match="atomic amount must be an int"):
            await _store(connection, value)


async def test_atomic_amount_column_rejects_values_wider_than_78_digits(
    db_engine: AsyncEngine,
) -> None:
    async with db_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="numeric field overflow"):
            await _store(connection, 10**78)


async def test_atomic_amount_rejects_fractional_results(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as connection:
        with pytest.raises(ValueError, match=r"atomic amount must be integral, got 1\.5"):
            await connection.scalar(text("SELECT 1.5 AS amount").columns(amount=AtomicAmount()))
