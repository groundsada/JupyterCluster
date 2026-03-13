"""Add per-user namespace management permission columns.

Revision ID: 002_namespace_permissions
Revises: 001_initial_schema
Create Date: 2026-03-11

Adds can_create_namespaces and can_delete_namespaces (nullable Boolean)
to the users table.  NULL means "inherit the global allowUserNamespaceManagement
setting", matching the three-level resolution (admin → per-user → global) used
in JupyterCluster's permission helpers.

Column existence is checked before adding so the migration is safe to replay.
"""

import sqlalchemy as sa
from alembic import op

revision = "002_namespace_permissions"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in cols


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        if not _has_column("users", "can_create_namespaces"):
            batch_op.add_column(
                sa.Column("can_create_namespaces", sa.Boolean, nullable=True, default=None)
            )
        if not _has_column("users", "can_delete_namespaces"):
            batch_op.add_column(
                sa.Column("can_delete_namespaces", sa.Boolean, nullable=True, default=None)
            )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        if _has_column("users", "can_delete_namespaces"):
            batch_op.drop_column("can_delete_namespaces")
        if _has_column("users", "can_create_namespaces"):
            batch_op.drop_column("can_create_namespaces")
