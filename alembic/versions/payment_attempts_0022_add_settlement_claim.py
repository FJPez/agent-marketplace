"""add the payment attempt settlement claim

Settling a payment is the one step that moves the payer's funds, so it gets a
durable claim of its own: the attempt is moved to ``settling`` with a lease
before the facilitator is called, and a settle whose outcome never came back
ends in ``settlement_unknown`` instead of looking retryable. The CHECK keeps the
lease on settling rows only, and the ledger unique constraint makes the three
entries for one attempt insertable exactly once.

This migration is schema-reversible only: the downgrade deletes every attempt
left in the two new statuses, because the older schema cannot express them.

Revision ID: payment_attempts_0022
Revises: invocations_0021
Create Date: 2026-09-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "payment_attempts_0022"
down_revision: str | None = "invocations_0021"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

STATUS_CHECK_NAME = "ck_payment_attempts_payment_attempt_status"
# The metadata naming convention expands this to ck_payment_attempts_lease_only_settling.
LEASE_CHECK_NAME = "lease_only_settling"
LEDGER_ENTRY_UNIQUE_NAME = "uq_ledger_entries_payment_attempt_entry_type"

EXPANDED_STATUS_CHECK = (
    "status IN ("
    "'challenged', "
    "'verified', "
    "'verify_failed', "
    "'settle_failed', "
    "'settled', "
    "'consumed', "
    "'compensation_required', "
    "'settling', "
    "'settlement_unknown'"
    ")"
)
LEGACY_STATUS_CHECK = (
    "status IN ("
    "'challenged', "
    "'verified', "
    "'verify_failed', "
    "'settle_failed', "
    "'settled', "
    "'consumed', "
    "'compensation_required'"
    ")"
)


def upgrade() -> None:
    op.execute(f"ALTER TABLE payment_attempts DROP CONSTRAINT IF EXISTS {STATUS_CHECK_NAME}")
    op.create_check_constraint(
        op.f(STATUS_CHECK_NAME),
        "payment_attempts",
        EXPANDED_STATUS_CHECK,
    )
    op.add_column(
        "payment_attempts",
        sa.Column("settle_in_progress_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        LEASE_CHECK_NAME,
        "payment_attempts",
        "status = 'settling' OR settle_in_progress_until IS NULL",
    )
    op.create_unique_constraint(
        LEDGER_ENTRY_UNIQUE_NAME,
        "ledger_entries",
        ["payment_attempt_id", "entry_type"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM payment_attempts WHERE status IN ('settling', 'settlement_unknown')")
    op.drop_constraint(LEDGER_ENTRY_UNIQUE_NAME, "ledger_entries", type_="unique")
    op.drop_constraint(LEASE_CHECK_NAME, "payment_attempts", type_="check")
    op.drop_column("payment_attempts", "settle_in_progress_until")
    op.execute(f"ALTER TABLE payment_attempts DROP CONSTRAINT IF EXISTS {STATUS_CHECK_NAME}")
    op.create_check_constraint(
        op.f(STATUS_CHECK_NAME),
        "payment_attempts",
        LEGACY_STATUS_CHECK,
    )
