from alembic.autogenerate import render_python_code
from alembic.operations import ops
from sqlalchemy import Column, Integer

from app.db.types import AtomicAmount, render_alembic_item


def test_render_alembic_item_renders_atomic_amount_column_as_plain_numeric() -> None:
    upgrade_ops = ops.UpgradeOps(
        ops=[
            ops.AddColumnOp(
                "ledger_entries",
                Column("amount", AtomicAmount(), nullable=False),
            ),
        ],
    )

    rendered = render_python_code(upgrade_ops, render_item=render_alembic_item)

    assert "sa.Numeric(precision=78, scale=0)" in rendered
    assert "app.db.types" not in rendered
    assert render_alembic_item("type", Integer(), None) is False
