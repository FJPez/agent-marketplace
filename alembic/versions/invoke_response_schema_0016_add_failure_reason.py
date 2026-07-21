"""add upstream response schema invocation failure reason

Revision ID: invoke_response_schema_0016
Revises: submission_hardening_0015
Create Date: 2026-07-21 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "invoke_response_schema_0016"
down_revision: str | None = "submission_hardening_0015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE invocations DROP CONSTRAINT IF EXISTS ck_invocations_invocation_failure_reason"
    )
    op.execute("ALTER TABLE invocations DROP CONSTRAINT IF EXISTS invocation_failure_reason")
    op.create_check_constraint(
        op.f("ck_invocations_invocation_failure_reason"),
        "invocations",
        (
            "failure_reason IS NULL OR failure_reason IN ("
            "'upstream_timeout', "
            "'upstream_transport', "
            "'upstream_response', "
            "'upstream_schema'"
            ")"
        ),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE invocations "
        "SET failure_reason = 'upstream_response' "
        "WHERE failure_reason = 'upstream_schema'"
    )
    op.execute(
        "ALTER TABLE invocations DROP CONSTRAINT IF EXISTS ck_invocations_invocation_failure_reason"
    )
    op.execute("ALTER TABLE invocations DROP CONSTRAINT IF EXISTS invocation_failure_reason")
    op.create_check_constraint(
        op.f("ck_invocations_invocation_failure_reason"),
        "invocations",
        (
            "failure_reason IS NULL OR failure_reason IN ("
            "'upstream_timeout', "
            "'upstream_transport', "
            "'upstream_response'"
            ")"
        ),
    )
