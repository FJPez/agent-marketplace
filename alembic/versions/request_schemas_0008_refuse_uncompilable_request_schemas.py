"""refuse uncompilable request schemas

Revision ID: request_schemas_0008
Revises: provider_domain_tokens_0007
Create Date: 2026-09-27 12:00:00.000000
"""

import json
from collections.abc import Sequence

import jsonschema_rs
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "request_schemas_0008"
down_revision: str | None = "provider_domain_tokens_0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # From here on a request schema is checked when it is saved, and the invoke path
    # compiles every stored one as below (app/core/request_schema_validation.py). Refuse
    # a stored schema it cannot compile rather than fail its invocations; nothing is
    # deployed, so only a local database can hold one (see the README).
    rows = op.get_bind().execute(
        sa.text("SELECT id, request_schema::text FROM service_endpoints ORDER BY id"),
    )
    uncompilable = []
    for endpoint_id, request_schema in rows:
        try:
            jsonschema_rs.Draft202012Validator(
                json.loads(request_schema),
                offline=True,
                # PATTERN_SIZE_LIMIT and PATTERN_DFA_SIZE_LIMIT at this revision.
                pattern_options=jsonschema_rs.RegexOptions(
                    size_limit=10 * 1024,
                    dfa_size_limit=64 * 1024,
                ),
            )
        # ValidationError, or ValueError past the library's recursion limit.
        except ValueError:
            uncompilable.append(endpoint_id)
    if uncompilable:
        msg = (
            f"service endpoints {uncompilable} have request schemas the invoke path cannot "
            "compile; reset the local database (README, Resetting a Local Database)"
        )
        raise RuntimeError(msg)


def downgrade() -> None:
    # The upgrade changes nothing.
    pass
