"""add the invocation execution lease column

The lease records how long the worker that claimed an in-progress invocation
is expected to still be working on it. It is advisory: an expired lease means
the outcome is unknown and recovery is required, never that the invocation
failed. The CHECK constraint keeps terminal rows lease-free.

No backfill: a NULL lease on an existing in-progress row also means recovery
is required, which is the honest reading of a row claimed before this column
existed.

Revision ID: invocations_0021
Revises: schema_alignment_0020
Create Date: 2026-09-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "invocations_0021"
down_revision: str | None = "schema_alignment_0020"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# The metadata naming convention expands this to ck_invocations_lease_only_in_progress.
LEASE_CHECK_NAME = "lease_only_in_progress"


def upgrade() -> None:
    op.add_column(
        "invocations",
        sa.Column("in_progress_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        LEASE_CHECK_NAME,
        "invocations",
        "status = 'in_progress' OR in_progress_until IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint(LEASE_CHECK_NAME, "invocations", type_="check")
    op.drop_column("invocations", "in_progress_until")
