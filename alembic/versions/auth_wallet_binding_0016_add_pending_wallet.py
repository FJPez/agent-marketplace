"""bind wallet change challenges to their proposed address

Revision ID: auth_wallet_binding_0016
Revises: submission_hardening_0015
Create Date: 2026-07-31 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "auth_wallet_binding_0016"
down_revision: str | None = "submission_hardening_0015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("pending_wallet_address", sa.String(length=42), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "pending_wallet_address")
