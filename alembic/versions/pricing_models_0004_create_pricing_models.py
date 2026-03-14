"""create pricing models

Revision ID: pricing_models_0004
Revises: service_health_0003
Create Date: 2026-03-14 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "pricing_models_0004"
down_revision: str | None = "service_health_0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

pricing_model_type = sa.Enum(
    "free",
    "fixed_per_call",
    name="pricing_model_type",
    create_constraint=True,
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "pricing_models",
        sa.Column("endpoint_id", sa.BigInteger(), nullable=False),
        sa.Column("pricing_type", pricing_model_type, nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            (
                "(pricing_type = 'free' AND amount_minor IS NULL AND currency IS NULL) OR "
                "(pricing_type = 'fixed_per_call' AND amount_minor IS NOT NULL "
                "AND currency IS NOT NULL)"
            ),
            name=op.f("ck_pricing_models_pricing_shape"),
        ),
        sa.CheckConstraint(
            "amount_minor IS NULL OR amount_minor > 0",
            name=op.f("ck_pricing_models_positive_amount_minor"),
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["service_endpoints.id"],
            name=op.f("fk_pricing_models_endpoint_id_service_endpoints"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("endpoint_id", name=op.f("pk_pricing_models")),
    )


def downgrade() -> None:
    op.drop_table("pricing_models")
