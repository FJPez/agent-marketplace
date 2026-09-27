"""drop upstream config

Revision ID: drop_upstream_config_0006
Revises: provider_signing_secrets_0005
Create Date: 2026-09-27 10:08:59.945413
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "drop_upstream_config_0006"
down_revision: str | None = "provider_signing_secrets_0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # The per-endpoint config held provider-chosen plaintext HMAC secrets, replaced by
    # the marketplace-issued provider_signing_secrets. Its check constraint goes with it.
    op.drop_column("provider_upstreams", "config")


def downgrade() -> None:
    op.add_column(
        "provider_upstreams",
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_provider_upstreams_config_json_object"),
        "provider_upstreams",
        "jsonb_typeof(config) = 'object'",
    )
