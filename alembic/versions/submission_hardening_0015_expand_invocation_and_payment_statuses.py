"""expand invocation and payment attempt statuses

Revision ID: submission_hardening_0015
Revises: x402_payment_0014
Create Date: 2026-03-18 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "submission_hardening_0015"
down_revision: str | None = "x402_payment_0014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "invocations",
        "status",
        existing_type=sa.String(length=9),
        type_=sa.String(length=11),
        existing_nullable=False,
    )
    op.execute("ALTER TABLE invocations DROP CONSTRAINT IF EXISTS ck_invocations_invocation_status")
    op.execute("ALTER TABLE invocations DROP CONSTRAINT IF EXISTS invocation_status")
    op.create_check_constraint(
        op.f("ck_invocations_invocation_status"),
        "invocations",
        "status IN ('in_progress', 'succeeded', 'failed')",
    )

    op.execute(
        "ALTER TABLE payment_attempts DROP CONSTRAINT IF EXISTS "
        "ck_payment_attempts_payment_attempt_status"
    )
    op.execute("ALTER TABLE payment_attempts DROP CONSTRAINT IF EXISTS payment_attempt_status")
    op.create_check_constraint(
        op.f("ck_payment_attempts_payment_attempt_status"),
        "payment_attempts",
        (
            "status IN ("
            "'challenged', "
            "'verified', "
            "'verify_failed', "
            "'settle_failed', "
            "'settled', "
            "'consumed', "
            "'compensation_required'"
            ")"
        ),
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE payment_attempts DROP CONSTRAINT IF EXISTS "
        "ck_payment_attempts_payment_attempt_status"
    )
    op.execute("ALTER TABLE payment_attempts DROP CONSTRAINT IF EXISTS payment_attempt_status")
    op.create_check_constraint(
        op.f("ck_payment_attempts_payment_attempt_status"),
        "payment_attempts",
        (
            "status IN ("
            "'challenged', "
            "'verify_failed', "
            "'settle_failed', "
            "'settled', "
            "'consumed', "
            "'compensation_required'"
            ")"
        ),
    )

    op.execute("ALTER TABLE invocations DROP CONSTRAINT IF EXISTS ck_invocations_invocation_status")
    op.execute("ALTER TABLE invocations DROP CONSTRAINT IF EXISTS invocation_status")
    op.create_check_constraint(
        op.f("ck_invocations_invocation_status"),
        "invocations",
        "status IN ('succeeded', 'failed')",
    )
    op.alter_column(
        "invocations",
        "status",
        existing_type=sa.String(length=11),
        type_=sa.String(length=9),
        existing_nullable=False,
    )
