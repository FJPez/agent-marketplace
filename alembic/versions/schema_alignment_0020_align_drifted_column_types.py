"""align drifted column types with the models

Two columns were created with types that never matched their models, leaving
``alembic check`` permanently reporting drift: ``invocations.response_payload``
was JSON while the model declares JSONB, and ``quotes.pricing_type`` was
VARCHAR(50) while the model declares a non-native enum, which compares as a
VARCHAR sized to its longest value.

Revision ID: schema_alignment_0020
Revises: service_health_0019
Create Date: 2026-09-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "schema_alignment_0020"
down_revision: str | None = "service_health_0019"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Length of the longest PricingModelType value ("fixed_per_call"), which is the
# width SQLAlchemy derives for the non-native enum column.
PRICING_TYPE_LENGTH = 14


def upgrade() -> None:
    op.alter_column(
        "invocations",
        "response_payload",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(),
        existing_nullable=True,
        postgresql_using="response_payload::jsonb",
    )
    op.alter_column(
        "quotes",
        "pricing_type",
        existing_type=sa.String(length=50),
        type_=sa.String(length=PRICING_TYPE_LENGTH),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "quotes",
        "pricing_type",
        existing_type=sa.String(length=PRICING_TYPE_LENGTH),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
    op.alter_column(
        "invocations",
        "response_payload",
        existing_type=postgresql.JSONB(),
        type_=sa.JSON(),
        existing_nullable=True,
        postgresql_using="response_payload::json",
    )
