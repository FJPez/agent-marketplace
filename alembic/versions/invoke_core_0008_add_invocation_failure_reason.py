"""add invocation failure reason

Revision ID: invoke_core_0008
Revises: x402_payment_0007
Create Date: 2026-03-15 21:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "invoke_core_0008"
down_revision: str | None = "x402_payment_0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invocations",
        sa.Column("failure_reason", sa.String(length=18), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_invocations_invocation_failure_reason"),
        "invocations",
        "failure_reason IN ('upstream_timeout', 'upstream_transport', 'upstream_response')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_invocations_invocation_failure_reason"),
        "invocations",
        type_="check",
    )
    op.drop_column("invocations", "failure_reason")
