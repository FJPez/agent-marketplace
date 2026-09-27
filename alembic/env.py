from __future__ import annotations

from logging.config import fileConfig
from typing import TYPE_CHECKING, Literal

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.db.models  # noqa: F401
from app.core.config import Settings
from app.db.base import Base
from app.db.types import AtomicAmount

if TYPE_CHECKING:
    from alembic.autogenerate.api import AutogenContext
    from sqlalchemy.engine import Connection

config = context.config


def render_item(type_: str, obj: object, autogen_context: AutogenContext) -> str | Literal[False]:
    """Render an AtomicAmount column as plain sa.Numeric.

    Autogenerate otherwise renders it as `app.db.types.AtomicAmount(...)` with
    no import for `app`, so the generated migration fails at import time and
    ties migrations to application code.
    """
    if type_ == "type" and isinstance(obj, AtomicAmount):
        return "sa.Numeric(precision=78, scale=0)"
    return False


# Programmatic callers that manage logging themselves opt out: re-running fileConfig
# would restore the INFO-level alembic logger the test session turns down.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Tests route the migration chain to a dedicated database through this attribute.
config.set_main_option(
    "sqlalchemy.url",
    config.attributes.get("database_url") or Settings().database_url,
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, render_item=render_item
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio

    asyncio.run(run_migrations_online())
