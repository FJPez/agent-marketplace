"""Column types shared by the ORM models."""

from decimal import Decimal
from typing import Literal

from sqlalchemy import Numeric
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class AtomicAmount(TypeDecorator[int]):
    """A token amount in atomic units (1 USDC = 1,000,000): an exact int, NUMERIC(78,0)."""

    impl = Numeric(78, 0)
    cache_ok = True

    def process_bind_param(self, value: int | None, dialect: Dialect) -> int | None:
        # bool is an int subclass, and a float has already lost precision.
        if value is not None and type(value) is not int:
            msg = f"atomic amount must be an int, got {type(value).__name__}"
            raise TypeError(msg)
        return value

    def process_result_value(self, value: Decimal | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if value != value.to_integral_value():
            msg = f"atomic amount must be integral, got {value}"
            raise ValueError(msg)
        return int(value)


def render_alembic_item(type_: str, obj: object, autogen_context: object) -> str | Literal[False]:
    """Keep generated migrations free of app imports by rendering AtomicAmount as sa.Numeric."""
    if type_ == "type" and isinstance(obj, AtomicAmount):
        return "sa.Numeric(precision=78, scale=0)"
    return False
