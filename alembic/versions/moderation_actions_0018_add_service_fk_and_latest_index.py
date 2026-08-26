"""add moderation actions service fk and latest-action index

Revision ID: moderation_actions_0018
Revises: endpoint_prices_0017
Create Date: 2026-08-27 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "moderation_actions_0018"
down_revision: str | None = "endpoint_prices_0017"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

SERVICE_FK_NAME = "fk_moderation_actions_service_id_services"
LATEST_INDEX_NAME = "ix_moderation_actions_service_id_id_desc"
PLAIN_INDEX_NAME = "ix_moderation_actions_service_id"


def upgrade() -> None:
    # The column carried no foreign key, so rows may reference deleted services.
    op.execute(
        """
        DELETE FROM moderation_actions
        WHERE service_id NOT IN (SELECT id FROM services)
        """
    )
    op.create_foreign_key(
        SERVICE_FK_NAME,
        "moderation_actions",
        "services",
        ["service_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute(f'CREATE INDEX "{LATEST_INDEX_NAME}" ON moderation_actions (service_id, id DESC)')
    op.drop_index(PLAIN_INDEX_NAME, table_name="moderation_actions")


def downgrade() -> None:
    op.create_index(PLAIN_INDEX_NAME, "moderation_actions", ["service_id"], unique=False)
    op.drop_index(LATEST_INDEX_NAME, table_name="moderation_actions")
    op.drop_constraint(SERVICE_FK_NAME, "moderation_actions", type_="foreignkey")
