from typing import TYPE_CHECKING, Literal

from alembic.autogenerate import render_python_code
from alembic.operations import ops
from sqlalchemy import Column

from app.db.types import AtomicAmount

if TYPE_CHECKING:
    from alembic.autogenerate.api import AutogenContext


# alembic/env.py cannot be imported directly (it shadows the installed
# `alembic` package and touches the database at module scope), so this
# mirrors its render_item exactly to pin the autogenerate rendering it relies
# on; alembic check against a real database (see docs) exercises the actual
# function end to end.
def _render_item(
    type_: str,
    obj: object,
    autogen_context: "AutogenContext",
) -> str | Literal[False]:
    if type_ == "type" and isinstance(obj, AtomicAmount):
        return "sa.Numeric(precision=78, scale=0)"
    return False


def test_render_item_renders_atomic_amount_column_as_plain_numeric() -> None:
    upgrade_ops = ops.UpgradeOps(
        ops=[
            ops.AddColumnOp(
                "ledger_entries",
                Column("amount", AtomicAmount(), nullable=False),
            ),
        ],
    )

    rendered = render_python_code(upgrade_ops, render_item=_render_item)

    assert "sa.Numeric(precision=78, scale=0)" in rendered
    assert "app.db.types" not in rendered
