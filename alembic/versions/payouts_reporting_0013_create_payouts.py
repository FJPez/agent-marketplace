"""create payouts

Revision ID: payouts_reporting_0013
Revises: auth_wallet_change_0012
Create Date: 2026-03-16 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "payouts_reporting_0013"
down_revision: str | None = "auth_wallet_change_0012"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = (
    "provider_services_0003",
    "invoke_core_0008",
    "x402_payment_0007",
)


def upgrade() -> None:
    op.create_table(
        "payouts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("provider_account_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("invocation_id", sa.BigInteger(), nullable=False),
        sa.Column("payment_attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("destination_wallet", sa.String(length=42), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("network", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ready",
                "pending",
                "sent",
                "failed",
                name="payout_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("transfer_reference", sa.String(length=255), nullable=True),
        sa.Column("request_idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "failure_code",
            sa.Enum(
                "executor_error",
                "invalid_amount",
                "wallet_not_configured",
                name="payout_failure_code",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("prepared_raw_transaction", sa.Text(), nullable=True),
        sa.Column("chain_nonce", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["invocations.id"],
            name=op.f("fk_payouts_invocation_id_invocations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"],
            ["payment_attempts.id"],
            name=op.f("fk_payouts_payment_attempt_id_payment_attempts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_account_id"],
            ["accounts.id"],
            name=op.f("fk_payouts_provider_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_payouts_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payouts")),
        sa.UniqueConstraint("payment_attempt_id", name=op.f("uq_payouts_payment_attempt_id")),
    )
    op.create_index(
        op.f("ix_payouts_provider_account_id"),
        "payouts",
        ["provider_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payouts_request_idempotency_key"),
        "payouts",
        ["request_idempotency_key"],
        unique=False,
    )
    op.create_index(op.f("ix_payouts_service_id"), "payouts", ["service_id"], unique=False)
    op.create_index(op.f("ix_payouts_invocation_id"), "payouts", ["invocation_id"], unique=False)
    op.create_index(op.f("ix_payouts_status"), "payouts", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payouts_status"), table_name="payouts")
    op.drop_index(op.f("ix_payouts_invocation_id"), table_name="payouts")
    op.drop_index(op.f("ix_payouts_service_id"), table_name="payouts")
    op.drop_index(op.f("ix_payouts_request_idempotency_key"), table_name="payouts")
    op.drop_index(op.f("ix_payouts_provider_account_id"), table_name="payouts")
    op.drop_table("payouts")
