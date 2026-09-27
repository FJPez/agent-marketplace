"""add provider signing secrets

Revision ID: provider_signing_secrets_0005
Revises: drop_endpoint_prices_0004
Create Date: 2026-09-27 10:00:18.148276
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "provider_signing_secrets_0005"
down_revision: str | None = "drop_endpoint_prices_0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_signing_secrets",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_ciphertext", sa.Text(), nullable=True),
        sa.Column("previous_expires_at", sa.DateTime(timezone=True), nullable=True),
        # The SHA-256 of the Idempotency-Key of the rotation that issued the current
        # secret, so a retried rotation returns that secret instead of rotating again.
        sa.Column("rotation_idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "(previous_ciphertext IS NULL) = (previous_expires_at IS NULL)",
            name=op.f("ck_provider_signing_secrets_previous_secret_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_provider_signing_secrets_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_provider_signing_secrets")),
    )


def downgrade() -> None:
    op.drop_table("provider_signing_secrets")
