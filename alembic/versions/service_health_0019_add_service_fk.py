"""add service health checks service fk

This migration is schema-reversible only: the downgrade drops the foreign key
but cannot restore orphan rows deleted on upgrade, nor rows later removed by
the cascade.

Revision ID: service_health_0019
Revises: moderation_actions_0018
Create Date: 2026-09-10 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "service_health_0019"
down_revision: str | None = "moderation_actions_0018"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

SERVICE_FK_NAME = "fk_service_health_checks_service_id_services"


def upgrade() -> None:
    # The column carried no foreign key, so rows may reference deleted services.
    op.execute(
        """
        DELETE FROM service_health_checks
        WHERE service_id NOT IN (SELECT id FROM services)
        """
    )
    op.create_foreign_key(
        SERVICE_FK_NAME,
        "service_health_checks",
        "services",
        ["service_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(SERVICE_FK_NAME, "service_health_checks", type_="foreignkey")
