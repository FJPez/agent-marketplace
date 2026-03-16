"""unify accounts and remove legacy identity profiles

Revision ID: auth_redesign_0010
Revises: ledger_earnings_0009
Create Date: 2026-03-16 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "auth_redesign_0010"
down_revision: str | None = "ledger_earnings_0009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("wallet_address", sa.String(length=42), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "account_type",
            sa.String(length=10),
            server_default=sa.text("'human'"),
            nullable=False,
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "display_name",
            sa.String(length=255),
            server_default=sa.text("'Anonymous'"),
            nullable=False,
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "nonce",
            sa.String(length=64),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "nonce_issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "token_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "accounts",
        sa.Column("wallet_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE accounts AS accounts
            SET display_name = COALESCE(
                provider_profiles.display_name,
                consumer_profiles.display_name,
                'Anonymous'
            )
            FROM provider_profiles
            FULL OUTER JOIN consumer_profiles
                ON consumer_profiles.account_id = provider_profiles.account_id
            WHERE accounts.id = COALESCE(
                provider_profiles.account_id,
                consumer_profiles.account_id
            )
            """
        )
    )
    op.create_index(op.f("ix_accounts_wallet_address"), "accounts", ["wallet_address"], unique=True)

    op.drop_constraint(
        op.f("fk_services_provider_account_id_provider_profiles"),
        "services",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_services_provider_account_id_accounts"),
        "services",
        "accounts",
        ["provider_account_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_table("consumer_profiles")
    op.drop_table("provider_profiles")


def downgrade() -> None:
    op.create_table(
        "provider_profiles",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(length=255),
            server_default=sa.text("'Anonymous'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_provider_profiles_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_provider_profiles")),
    )
    op.create_table(
        "consumer_profiles",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(length=255),
            server_default=sa.text("'Anonymous'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_consumer_profiles_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_consumer_profiles")),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO provider_profiles (account_id, created_at, display_name)
            SELECT id, created_at, display_name
            FROM accounts
            ON CONFLICT (account_id) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO consumer_profiles (account_id, created_at, display_name)
            SELECT id, created_at, display_name
            FROM accounts
            ON CONFLICT (account_id) DO NOTHING
            """
        )
    )

    op.drop_constraint(
        op.f("fk_services_provider_account_id_accounts"),
        "services",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_services_provider_account_id_provider_profiles"),
        "services",
        "provider_profiles",
        ["provider_account_id"],
        ["account_id"],
        ondelete="CASCADE",
    )

    op.drop_index(op.f("ix_accounts_wallet_address"), table_name="accounts")
    op.drop_column("accounts", "updated_at")
    op.drop_column("accounts", "wallet_changed_at")
    op.drop_column("accounts", "token_version")
    op.drop_column("accounts", "nonce_issued_at")
    op.drop_column("accounts", "nonce")
    op.drop_column("accounts", "display_name")
    op.drop_column("accounts", "account_type")
    op.drop_column("accounts", "wallet_address")
