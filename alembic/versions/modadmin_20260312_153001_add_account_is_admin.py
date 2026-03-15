"""add is_admin column to accounts table

Revision ID: modadmin_20260312_153001
Revises: modadmin_20260312_153000
Create Date: 2026-03-12 15:30:01.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "modadmin_20260312_153001"
down_revision: str | None = "pricing_models_0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "is_admin")
