"""Alembic environment configuration for JupyterCluster.

Mirrors JupyterHub's alembic/env.py:
- Imports ORM metadata so Alembic can diff the schema
- Supports both offline (URL-only) and online (live connection) modes
- render_as_batch=True is required for SQLite, which does not support
  ALTER TABLE natively; batch mode rewrites the table instead
- transaction_per_migration=True gives each migration its own transaction,
  matching JupyterHub's setting for predictable rollback behaviour
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import ORM Base so Alembic can compare target vs. current schema
from jupytercluster import orm

target_metadata = orm.Base.metadata

_common_opts = dict(
    target_metadata=target_metadata,
    # Each migration runs in its own transaction (matches JupyterHub)
    transaction_per_migration=True,
    # Required for SQLite: rewrites tables instead of ALTER TABLE
    render_as_batch=True,
)


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (offline mode).

    Alembic emits SQL to stdout rather than executing it.  Useful for
    generating migration scripts to be reviewed before applying.
    """
    conn = context.config.attributes.get("connection", None)
    opts = dict(_common_opts)

    if conn is None:
        opts["url"] = context.config.get_main_option("sqlalchemy.url")
        opts["literal_binds"] = True
    else:
        opts["connection"] = conn

    context.configure(**opts)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection (online mode)."""
    conn = context.config.attributes.get("connection", None)
    opts = dict(_common_opts)

    if conn is None:
        connectable = engine_from_config(
            context.config.get_section(context.config.config_ini_section),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            opts["connection"] = connection
            context.configure(**opts)
            with context.begin_transaction():
                context.run_migrations()
    else:
        opts["connection"] = conn
        context.configure(**opts)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
