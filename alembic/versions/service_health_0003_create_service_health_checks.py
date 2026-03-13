"""create service health checks

Revision ID: service_health_0003
Revises: 0002
Create Date: 2026-03-12 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "service_health_0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_health_checks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("check_name", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pass",
                "fail",
                "error",
                name="service_health_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_health_checks")),
    )
    op.create_index(
        "ix_service_health_checks_service_id_check_name_checked_at",
        "service_health_checks",
        ["service_id", "check_name", "checked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_service_health_checks_service_id_check_name_checked_at",
        table_name="service_health_checks",
    )
    op.drop_table("service_health_checks")
