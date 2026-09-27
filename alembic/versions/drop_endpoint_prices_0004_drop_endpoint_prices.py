"""drop endpoint prices

Revision ID: drop_endpoint_prices_0004
Revises: listing_prices_0003
Create Date: 2026-09-27 07:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "drop_endpoint_prices_0004"
down_revision: str | None = "listing_prices_0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # A USD-cent price has no asset, network or treasury pay_to, so it cannot become
    # a price version. Refuse instead of silently unpricing paid endpoints; nothing is
    # deployed, so only a local database can hold such rows (see the README).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM endpoint_prices) THEN
                RAISE EXCEPTION 'endpoint_prices holds USD-cent prices that listing_prices '
                    'cannot take over; reset the local database '
                    '(README, Resetting a Local Database)';
            END IF;
        END
        $$
        """,
    )
    op.drop_table("endpoint_prices")


def downgrade() -> None:
    # Recreated empty: a price version has no USD-cent equivalent.
    op.create_table(
        "endpoint_prices",
        sa.Column("endpoint_id", sa.BigInteger(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
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
            "amount_minor > 0",
            name=op.f("ck_endpoint_prices_positive_amount_minor"),
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["service_endpoints.id"],
            name=op.f("fk_endpoint_prices_endpoint_id_service_endpoints"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("endpoint_id", name=op.f("pk_endpoint_prices")),
    )
