"""create provider service tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-12 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

service_lifecycle = sa.Enum(
    "draft",
    "active",
    "suspended",
    "delisted",
    name="service_lifecycle",
    native_enum=False,
)
access_mode = sa.Enum(
    "free",
    "paid",
    name="access_mode",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("provider_account_id", sa.BigInteger(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "lifecycle",
            service_lifecycle,
            nullable=False,
            server_default="draft",
        ),
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
            ["provider_account_id"],
            ["provider_profiles.account_id"],
            name=op.f("fk_services_provider_account_id_provider_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
        sa.UniqueConstraint("slug", name=op.f("uq_services_slug")),
    )
    op.create_index(op.f("ix_services_provider_account_id"), "services", ["provider_account_id"])

    op.create_table(
        "service_tags",
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_service_tags_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("service_id", "tag", name=op.f("pk_service_tags")),
    )

    op.create_table(
        "service_endpoints",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("access_mode", access_mode, nullable=False),
        sa.Column(
            "request_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "response_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "jsonb_typeof(request_schema) = 'object'",
            name=op.f("ck_service_endpoints_request_schema_json_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(response_schema) = 'object'",
            name=op.f("ck_service_endpoints_response_schema_json_object"),
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_service_endpoints_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_endpoints")),
        sa.UniqueConstraint("service_id", "key", name=op.f("uq_service_endpoints_service_id")),
    )
    op.create_index(op.f("ix_service_endpoints_service_id"), "service_endpoints", ["service_id"])

    op.create_table(
        "provider_upstreams",
        sa.Column("endpoint_id", sa.BigInteger(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("http_method", sa.String(length=16), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name=op.f("ck_provider_upstreams_config_json_object"),
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["service_endpoints.id"],
            name=op.f("fk_provider_upstreams_endpoint_id_service_endpoints"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("endpoint_id", name=op.f("pk_provider_upstreams")),
    )


def downgrade() -> None:
    op.drop_table("provider_upstreams")
    op.drop_index(op.f("ix_service_endpoints_service_id"), table_name="service_endpoints")
    op.drop_table("service_endpoints")
    op.drop_table("service_tags")
    op.drop_index(op.f("ix_services_provider_account_id"), table_name="services")
    op.drop_table("services")
