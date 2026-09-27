"""drop unused lifecycle values

Revision ID: service_lifecycle_0009
Revises: request_schemas_0008
Create Date: 2026-09-27 12:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "service_lifecycle_0009"
down_revision: str | None = "request_schemas_0008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Suspending and delisting are moderation actions (moderation_actions); the
    # lifecycle's own suspended and delisted values were never written. Refuse a row
    # that holds one instead of failing on the narrower column; nothing is deployed,
    # so only a local database can hold such a row (see the README).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM services WHERE lifecycle NOT IN ('draft', 'active')) THEN
                RAISE EXCEPTION 'services holds the retired lifecycle values suspended or '
                    'delisted, which only moderation actions record now; reset the local '
                    'database (README, Resetting a Local Database)';
            END IF;
        END
        $$
        """,
    )
    _replace_lifecycle_column(length=6, values="'draft', 'active'")


def downgrade() -> None:
    _replace_lifecycle_column(length=9, values="'draft', 'active', 'suspended', 'delisted'")


def _replace_lifecycle_column(*, length: int, values: str) -> None:
    op.drop_constraint(op.f("ck_services_service_lifecycle"), "services", type_="check")
    op.alter_column(
        "services",
        "lifecycle",
        type_=sa.String(length=length),
        existing_nullable=False,
        existing_server_default="draft",
    )
    op.create_check_constraint(
        op.f("ck_services_service_lifecycle"),
        "services",
        f"lifecycle IN ({values})",
    )
