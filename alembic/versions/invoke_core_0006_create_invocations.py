"""create invocations

Revision ID: invoke_core_0006
Revises: d42985996a62
Create Date: 2026-03-15 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "invoke_core_0006"
down_revision: str | None = "d42985996a62"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invocations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("consumer_account_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("endpoint_id", sa.BigInteger(), nullable=False),
        sa.Column("endpoint_key", sa.String(length=255), nullable=False),
        sa.Column("access_mode", sa.String(length=4), nullable=False),
        sa.Column("quote_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=9), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("upstream_status_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["consumer_account_id"],
            ["accounts.id"],
            name=op.f("fk_invocations_consumer_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["service_endpoints.id"],
            name=op.f("fk_invocations_endpoint_id_service_endpoints"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            name=op.f("fk_invocations_quote_id_quotes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_invocations_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invocations")),
        sa.UniqueConstraint(
            "consumer_account_id",
            "idempotency_key",
            name=op.f("uq_invocations_consumer_account_id"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_invocations_access_mode"),
        "invocations",
        "access_mode IN ('free', 'paid')",
    )
    op.create_check_constraint(
        op.f("ck_invocations_invocation_status"),
        "invocations",
        "status IN ('succeeded', 'failed')",
    )
    op.create_index(
        op.f("ix_invocations_consumer_account_id"),
        "invocations",
        ["consumer_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invocations_endpoint_id"), "invocations", ["endpoint_id"], unique=False
    )
    op.create_index(op.f("ix_invocations_quote_id"), "invocations", ["quote_id"], unique=False)
    op.create_index(op.f("ix_invocations_service_id"), "invocations", ["service_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_invocations_service_id"), table_name="invocations")
    op.drop_index(op.f("ix_invocations_quote_id"), table_name="invocations")
    op.drop_index(op.f("ix_invocations_endpoint_id"), table_name="invocations")
    op.drop_index(op.f("ix_invocations_consumer_account_id"), table_name="invocations")
    op.drop_table("invocations")
