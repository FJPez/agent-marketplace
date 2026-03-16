"""create wallet change log

Revision ID: auth_wallet_change_0012
Revises: auth_api_keys_0011
Create Date: 2026-03-16 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "auth_wallet_change_0012"
down_revision: str | None = "auth_api_keys_0011"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wallet_change_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("previous_wallet_address", sa.String(length=42), nullable=False),
        sa.Column("new_wallet_address", sa.String(length=42), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_wallet_change_log_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallet_change_log")),
    )
    op.create_index(
        op.f("ix_wallet_change_log_account_id"),
        "wallet_change_log",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_wallet_change_log_account_id"), table_name="wallet_change_log")
    op.drop_table("wallet_change_log")
