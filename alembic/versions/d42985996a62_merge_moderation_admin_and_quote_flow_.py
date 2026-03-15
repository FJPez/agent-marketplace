"""merge moderation-admin and quote-flow heads

Revision ID: d42985996a62
Revises: modadmin_20260312_153001, quotes_0005
Create Date: 2026-03-15 00:44:45.319213
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d42985996a62"
down_revision: str | None = ("modadmin_20260312_153001", "quotes_0005")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
