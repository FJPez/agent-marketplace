"""create quotes

Revision ID: quotes_0005
Revises: pricing_models_0004
Create Date: 2026-03-14 22:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "quotes_0005"
down_revision: str | None = "pricing_models_0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quotes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("endpoint_id", sa.BigInteger(), nullable=False),
        sa.Column("endpoint_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("pricing_type", sa.String(length=14), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("service_revision_id", sa.BigInteger(), nullable=True),
        sa.Column("service_change_token", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["service_endpoints.id"],
            name=op.f("fk_quotes_endpoint_id_service_endpoints"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_quotes_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_revision_id"],
            ["service_revisions.id"],
            name=op.f("fk_quotes_service_revision_id_service_revisions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotes")),
    )
    op.create_index(op.f("ix_quotes_endpoint_id"), "quotes", ["endpoint_id"], unique=False)
    op.create_index(op.f("ix_quotes_service_id"), "quotes", ["service_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_quotes_service_id"), table_name="quotes")
    op.drop_index(op.f("ix_quotes_endpoint_id"), table_name="quotes")
    op.drop_table("quotes")
