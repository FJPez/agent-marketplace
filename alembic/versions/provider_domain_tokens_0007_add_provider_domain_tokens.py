"""add provider domain tokens

Revision ID: provider_domain_tokens_0007
Revises: drop_upstream_config_0006
Create Date: 2026-09-27 10:22:04.922235
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "provider_domain_tokens_0007"
down_revision: str | None = "drop_upstream_config_0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_domain_tokens",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_provider_domain_tokens_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_provider_domain_tokens")),
    )


def downgrade() -> None:
    op.drop_table("provider_domain_tokens")
