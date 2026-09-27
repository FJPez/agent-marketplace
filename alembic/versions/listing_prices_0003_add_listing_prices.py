"""add listing prices

Revision ID: listing_prices_0003
Revises: endpoint_fields_0002
Create Date: 2026-09-27 07:16:12.698259
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "listing_prices_0003"
down_revision: str | None = "endpoint_fields_0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "listing_prices",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("endpoint_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=78, scale=0), nullable=False),
        sa.Column("asset", sa.String(length=42), nullable=False),
        sa.Column("network", sa.String(length=41), nullable=False),
        sa.Column("pay_to", sa.String(length=42), nullable=False),
        sa.Column("max_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("fee_bps", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_listing_prices_positive_amount")),
        sa.CheckConstraint(
            "fee_bps BETWEEN 0 AND 10000",
            name=op.f("ck_listing_prices_fee_bps_range"),
        ),
        sa.CheckConstraint(
            "max_timeout_seconds > 0",
            name=op.f("ck_listing_prices_positive_max_timeout_seconds"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_listing_prices_positive_version")),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["service_endpoints.id"],
            name="fk_listing_prices_endpoint_id_service_endpoints",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listing_prices")),
        sa.UniqueConstraint(
            "endpoint_id",
            "version",
            name="uq_listing_prices_endpoint_id_version",
        ),
    )
    op.add_column(
        "service_endpoints",
        sa.Column("current_price_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_service_endpoints_current_price_id_listing_prices",
        "service_endpoints",
        "listing_prices",
        ["current_price_id"],
        ["id"],
    )
    op.create_check_constraint(
        op.f("ck_service_endpoints_free_has_no_price"),
        "service_endpoints",
        "access_mode = 'paid' OR current_price_id IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_service_endpoints_free_has_no_price"),
        "service_endpoints",
        type_="check",
    )
    op.drop_constraint(
        "fk_service_endpoints_current_price_id_listing_prices",
        "service_endpoints",
        type_="foreignkey",
    )
    op.drop_column("service_endpoints", "current_price_id")
    op.drop_table("listing_prices")
