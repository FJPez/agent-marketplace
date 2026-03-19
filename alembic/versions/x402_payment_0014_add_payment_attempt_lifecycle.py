"""add payment attempt lifecycle fields

Revision ID: x402_payment_0014
Revises: payouts_reporting_0013
Create Date: 2026-03-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "x402_payment_0014"
down_revision: str | None = "payouts_reporting_0013"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


payment_attempt_status = sa.Enum(
    "challenged",
    "verify_failed",
    "settle_failed",
    "settled",
    "consumed",
    "compensation_required",
    name="payment_attempt_status",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    bind = op.get_bind()
    payment_attempt_status.create(bind, checkfirst=True)
    op.add_column(
        "payment_attempts",
        sa.Column(
            "status",
            payment_attempt_status,
            nullable=False,
            server_default="challenged",
        ),
    )
    op.add_column(
        "payment_attempts",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute("UPDATE payment_attempts SET updated_at = created_at WHERE updated_at IS NULL")


def downgrade() -> None:
    op.drop_column("payment_attempts", "updated_at")
    op.drop_column("payment_attempts", "status")
    bind = op.get_bind()
    payment_attempt_status.drop(bind, checkfirst=True)
