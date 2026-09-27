"""Column types shared by the ORM models."""

from decimal import Decimal

from sqlalchemy import Numeric
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class AtomicAmount(TypeDecorator[int]):
    """A token amount in the asset's atomic units (1 USDC = 1,000,000), as an exact int.

    Stored as NUMERIC(78,0), which holds any uint256. asyncpg reads NUMERIC as
    Decimal; the conversion to int never goes through float. Amounts are
    signed: ledger entries carry a sign (debits positive).
    """

    impl = Numeric(78, 0)
    cache_ok = True

    def process_bind_param(self, value: int | None, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        # bool is an int subclass, and a float has already lost precision.
        if type(value) is not int:
            msg = f"atomic amount must be an int, got {type(value).__name__}"
            raise TypeError(msg)
        return Decimal(value)

    def process_result_value(self, value: Decimal | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if value != value.to_integral_value():
            msg = f"atomic amount must be integral, got {value}"
            raise ValueError(msg)
        return int(value)
