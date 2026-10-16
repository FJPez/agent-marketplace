"""add endpoint invocation fields

Revision ID: endpoint_fields_0002
Revises: baseline_0001
Create Date: 2026-09-27 07:08:40.056424
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "endpoint_fields_0002"
down_revision: str | None = "baseline_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_endpoints",
        sa.Column(
            "response_content_type",
            sa.String(length=255),
            server_default="application/json",
            nullable=False,
        ),
    )
    op.add_column(
        "service_endpoints",
        sa.Column(
            "supports_idempotency",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    # Endpoints saved under the old 3600 s cap keep working at the new maximum.
    op.execute("UPDATE service_endpoints SET timeout_seconds = 30 WHERE timeout_seconds > 30")
    op.create_check_constraint(
        op.f("ck_service_endpoints_timeout_seconds_range"),
        "service_endpoints",
        "timeout_seconds BETWEEN 1 AND 30",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_service_endpoints_timeout_seconds_range"),
        "service_endpoints",
        type_="check",
    )
    op.drop_column("service_endpoints", "supports_idempotency")
    op.drop_column("service_endpoints", "response_content_type")
