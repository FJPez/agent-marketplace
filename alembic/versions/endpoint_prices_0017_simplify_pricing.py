"""simplify endpoint pricing storage

Revision ID: endpoint_prices_0017
Revises: auth_wallet_binding_0016
Create Date: 2026-08-25 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "endpoint_prices_0017"
down_revision: str | None = "auth_wallet_binding_0016"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

pricing_model_type = sa.Enum(
    "free",
    "fixed_per_call",
    name="pricing_model_type",
    create_constraint=True,
    native_enum=False,
)

PRICING_SHAPE_CHECK = (
    "(pricing_type = 'free' AND amount_minor IS NULL AND currency IS NULL) OR "
    "(pricing_type = 'fixed_per_call' AND amount_minor IS NOT NULL "
    "AND currency IS NOT NULL)"
)

RENAMED_CONSTRAINTS = (
    ("pk_pricing_models", "pk_endpoint_prices"),
    (
        "fk_pricing_models_endpoint_id_service_endpoints",
        "fk_endpoint_prices_endpoint_id_service_endpoints",
    ),
)


def upgrade() -> None:
    op.execute("DELETE FROM pricing_models WHERE pricing_type = 'free'")
    op.drop_constraint("pricing_shape", "pricing_models", type_="check")
    op.drop_column("pricing_models", "pricing_type")
    op.alter_column("pricing_models", "amount_minor", nullable=False)
    op.alter_column("pricing_models", "currency", nullable=False)
    op.drop_constraint("positive_amount_minor", "pricing_models", type_="check")
    op.rename_table("pricing_models", "endpoint_prices")
    for old_name, new_name in RENAMED_CONSTRAINTS:
        op.execute(f'ALTER TABLE endpoint_prices RENAME CONSTRAINT "{old_name}" TO "{new_name}"')
    op.create_check_constraint("positive_amount_minor", "endpoint_prices", "amount_minor > 0")


def downgrade() -> None:
    op.drop_constraint("positive_amount_minor", "endpoint_prices", type_="check")
    for old_name, new_name in RENAMED_CONSTRAINTS:
        op.execute(f'ALTER TABLE endpoint_prices RENAME CONSTRAINT "{new_name}" TO "{old_name}"')
    op.rename_table("endpoint_prices", "pricing_models")
    op.create_check_constraint(
        "positive_amount_minor",
        "pricing_models",
        "amount_minor IS NULL OR amount_minor > 0",
    )
    op.add_column("pricing_models", sa.Column("pricing_type", pricing_model_type, nullable=True))
    op.execute("UPDATE pricing_models SET pricing_type = 'fixed_per_call'")
    op.alter_column("pricing_models", "pricing_type", nullable=False)
    op.alter_column("pricing_models", "amount_minor", nullable=True)
    op.alter_column("pricing_models", "currency", nullable=True)
    op.create_check_constraint(
        "pricing_shape",
        "pricing_models",
        PRICING_SHAPE_CHECK,
    )
    op.execute(
        """
        INSERT INTO pricing_models (endpoint_id, pricing_type)
        SELECT se.id, 'free'
        FROM service_endpoints se
        WHERE se.access_mode = 'free'
          AND NOT EXISTS (
              SELECT 1 FROM pricing_models pm WHERE pm.endpoint_id = se.id
          )
        """
    )
