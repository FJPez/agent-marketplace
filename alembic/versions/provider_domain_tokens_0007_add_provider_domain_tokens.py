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
    # gen_random_uuid() is volatile, so every existing account gets its own token.
    op.add_column(
        "accounts",
        sa.Column(
            "domain_token",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "domain_token")
