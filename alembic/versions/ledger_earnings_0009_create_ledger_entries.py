"""create ledger entries

Revision ID: ledger_earnings_0009
Revises: invoke_core_0008
Create Date: 2026-03-16 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ledger_earnings_0009"
down_revision: str | None = "invoke_core_0008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


IMMUTABLE_LEDGER_FN = "prevent_ledger_entries_mutation"


def upgrade() -> None:
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("provider_account_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("invocation_id", sa.BigInteger(), nullable=False),
        sa.Column("payment_attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_type", sa.String(length=16), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entry_type IN ('charge', 'platform_fee', 'provider_earning', 'refund')",
            name=op.f("ck_ledger_entries_ledger_entry_type"),
        ),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["invocations.id"],
            name=op.f("fk_ledger_entries_invocation_id_invocations"),
        ),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"],
            ["payment_attempts.id"],
            name=op.f("fk_ledger_entries_payment_attempt_id_payment_attempts"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_account_id"],
            ["accounts.id"],
            name=op.f("fk_ledger_entries_provider_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_ledger_entries_service_id_services"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
    )
    op.create_index(
        op.f("ix_ledger_entries_invocation_id"),
        "ledger_entries",
        ["invocation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_entries_payment_attempt_id"),
        "ledger_entries",
        ["payment_attempt_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_entries_provider_account_id"),
        "ledger_entries",
        ["provider_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_entries_service_id"),
        "ledger_entries",
        ["service_id"],
        unique=False,
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {IMMUTABLE_LEDGER_FN}()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'ledger entries are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER ledger_entries_no_update
            BEFORE UPDATE ON ledger_entries
            FOR EACH ROW
            EXECUTE FUNCTION {IMMUTABLE_LEDGER_FN}()
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER ledger_entries_no_delete
            BEFORE DELETE ON ledger_entries
            FOR EACH ROW
            EXECUTE FUNCTION {IMMUTABLE_LEDGER_FN}()
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS ledger_entries_no_delete ON ledger_entries"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS ledger_entries_no_update ON ledger_entries"))
    op.execute(sa.text(f"DROP FUNCTION IF EXISTS {IMMUTABLE_LEDGER_FN}()"))
    op.drop_index(op.f("ix_ledger_entries_service_id"), table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_provider_account_id"), table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_payment_attempt_id"), table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_invocation_id"), table_name="ledger_entries")
    op.drop_table("ledger_entries")
