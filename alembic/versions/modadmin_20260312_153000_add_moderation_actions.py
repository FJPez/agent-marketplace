"""add moderation actions table

Revision ID: modadmin_20260312_153000
Revises: 0002
Create Date: 2026-03-12 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "modadmin_20260312_153000"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "moderation_actions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_account_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('suspend', 'restore', 'delist')",
            name=op.f("ck_moderation_actions_action_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_account_id"],
            ["accounts.id"],
            name=op.f("fk_moderation_actions_actor_account_id_accounts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_actions")),
    )
    op.create_index(
        op.f("ix_moderation_actions_service_id"),
        "moderation_actions",
        ["service_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_moderation_actions_service_id"), table_name="moderation_actions")
    op.drop_table("moderation_actions")
