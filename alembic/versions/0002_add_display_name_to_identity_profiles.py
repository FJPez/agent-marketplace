"""add display names to identity profiles

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-11 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

PROVIDER_PLACEHOLDER = "Unknown Provider"
CONSUMER_PLACEHOLDER = "Unknown Consumer"


def upgrade() -> None:
    op.add_column(
        "provider_profiles",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "consumer_profiles",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE provider_profiles SET display_name = :display_name WHERE display_name IS NULL",
        ).bindparams(display_name=PROVIDER_PLACEHOLDER),
    )
    op.execute(
        sa.text(
            "UPDATE consumer_profiles SET display_name = :display_name WHERE display_name IS NULL",
        ).bindparams(display_name=CONSUMER_PLACEHOLDER),
    )
    op.alter_column(
        "provider_profiles",
        "display_name",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.alter_column(
        "consumer_profiles",
        "display_name",
        existing_type=sa.String(length=255),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("consumer_profiles", "display_name")
    op.drop_column("provider_profiles", "display_name")
