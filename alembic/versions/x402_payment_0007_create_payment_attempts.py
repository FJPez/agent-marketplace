"""create payment attempts

Revision ID: x402_payment_0007
Revises: invoke_core_0006
Create Date: 2026-03-15 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "x402_payment_0007"
down_revision: str | None = "invoke_core_0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("consumer_account_id", sa.BigInteger(), nullable=False),
        sa.Column("quote_id", sa.BigInteger(), nullable=False),
        sa.Column("invocation_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payment_identifier", sa.String(length=255), nullable=True),
        sa.Column("payment_requirement", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payment_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verify_outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("settle_outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("facilitator_reference", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["consumer_account_id"],
            ["accounts.id"],
            name=op.f("fk_payment_attempts_consumer_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["invocations.id"],
            name=op.f("fk_payment_attempts_invocation_id_invocations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            name=op.f("fk_payment_attempts_quote_id_quotes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_attempts")),
        sa.UniqueConstraint(
            "payment_identifier", name=op.f("uq_payment_attempts_payment_identifier")
        ),
    )
    op.create_index(
        op.f("ix_payment_attempts_consumer_account_id"),
        "payment_attempts",
        ["consumer_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_attempts_invocation_id"),
        "payment_attempts",
        ["invocation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_attempts_quote_id"),
        "payment_attempts",
        ["quote_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_attempts_quote_id"), table_name="payment_attempts")
    op.drop_index(op.f("ix_payment_attempts_invocation_id"), table_name="payment_attempts")
    op.drop_index(op.f("ix_payment_attempts_consumer_account_id"), table_name="payment_attempts")
    op.drop_table("payment_attempts")
