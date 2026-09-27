"""add payout addresses

Revision ID: payout_addresses_0010
Revises: service_lifecycle_0009
Create Date: 2026-09-27 13:39:22.607176
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "payout_addresses_0010"
down_revision: str | None = "service_lifecycle_0009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payout_address_challenges",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("network", sa.String(length=41), nullable=False),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_payout_address_challenges_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_payout_address_challenges")),
    )
    op.create_table(
        "payout_addresses",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("network", sa.String(length=41), nullable=False),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=132), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_payout_addresses_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payout_addresses")),
    )
    op.create_index(
        "ix_payout_addresses_account_id_network_id",
        "payout_addresses",
        ["account_id", "network", "id"],
    )
    # Proofs are immutable: an updated address or effective_at would redirect payouts
    # without a proof or a hold, so a change inserts a new proof. Deletes stay allowed
    # for the account cascade. Autogenerate does not compare triggers, so only this
    # migration knows about them.
    op.execute(
        """
        CREATE FUNCTION reject_payout_address_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'payout_addresses rows are immutable; prove a new address instead';
        END
        $$
        """,
    )
    op.execute(
        """
        CREATE TRIGGER payout_addresses_immutable
        BEFORE UPDATE ON payout_addresses
        FOR EACH ROW EXECUTE FUNCTION reject_payout_address_update()
        """,
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER payout_addresses_immutable ON payout_addresses")
    op.execute("DROP FUNCTION reject_payout_address_update()")
    op.drop_table("payout_addresses")
    op.drop_table("payout_address_challenges")
