"""Initial schema — hubs, hub_events, users, config.

Revision ID: 001_initial_schema
Revises: (none — first migration)
Create Date: 2026-03-11

Idempotent: each CREATE TABLE is guarded by an existence check so this
migration is safe to run against a database that was bootstrapped with
SQLAlchemy's create_all() before Alembic was introduced (matches the
approach used in JupyterHub's earliest migrations).
"""

import sqlalchemy as sa
from alembic import op

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if not _table_exists("hubs"):
        op.create_table(
            "hubs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(255), unique=True, nullable=False, index=True),
            sa.Column("namespace", sa.String(255), unique=True, nullable=False, index=True),
            sa.Column("owner", sa.String(255), nullable=False, index=True),
            sa.Column("helm_release_name", sa.String(255), unique=True, nullable=False),
            sa.Column("helm_chart", sa.String(255), default="jupyterhub/jupyterhub"),
            sa.Column("helm_chart_version", sa.String(50)),
            sa.Column("values", sa.JSON, default=dict),
            sa.Column("status", sa.String(50), default="pending"),
            sa.Column("url", sa.String(500)),
            sa.Column("error_message", sa.Text),
            sa.Column("created", sa.DateTime),
            sa.Column("last_activity", sa.DateTime),
            sa.Column("description", sa.Text),
        )

    if not _table_exists("hub_events"):
        op.create_table(
            "hub_events",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column(
                "hub_id",
                sa.Integer,
                sa.ForeignKey("hubs.id"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("message", sa.Text),
            sa.Column("timestamp", sa.DateTime, index=True),
        )

    if not _table_exists("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(255), unique=True, nullable=False, index=True),
            sa.Column("admin", sa.Boolean, nullable=False, default=False),
            sa.Column("allowed_namespaces", sa.JSON, default=list),
            sa.Column("max_hubs", sa.Integer),
            sa.Column("created", sa.DateTime),
            sa.Column("last_activity", sa.DateTime),
        )

    if not _table_exists("config"):
        op.create_table(
            "config",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("key", sa.String(255), unique=True, nullable=False, index=True),
            sa.Column("value", sa.Text, nullable=False),
        )


def downgrade() -> None:
    # Drop in reverse dependency order
    for table in ("hub_events", "hubs", "users", "config"):
        if _table_exists(table):
            op.drop_table(table)
