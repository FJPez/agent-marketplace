"""create service revisions

Revision ID: service_revisions_0004
Revises: service_health_0003
Create Date: 2026-03-14 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "service_revisions_0004"
down_revision: str | None = "service_health_0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_revisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("change_token", sa.String(length=64), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name=op.f("ck_service_revisions_snapshot_json_object"),
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_service_revisions_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_revisions")),
        sa.UniqueConstraint(
            "service_id",
            "revision_number",
            name=op.f("uq_service_revisions_service_id"),
        ),
    )
    op.create_index(
        op.f("ix_service_revisions_service_id"),
        "service_revisions",
        ["service_id"],
    )

    op.add_column(
        "services",
        sa.Column("current_revision_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "services",
        sa.Column("current_change_token", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_services_current_revision_id_service_revisions"),
        "services",
        "service_revisions",
        ["current_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_services_current_revision_id_service_revisions"),
        "services",
        type_="foreignkey",
    )
    op.drop_column("services", "current_change_token")
    op.drop_column("services", "current_revision_id")
    op.drop_index(
        op.f("ix_service_revisions_service_id"),
        table_name="service_revisions",
    )
    op.drop_table("service_revisions")
