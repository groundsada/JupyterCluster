"""Create api_tokens table.

Revision ID: 003_api_tokens
Revises: 002_namespace_permissions
Create Date: 2026-03-11

Adds the api_tokens table that backs Bearer-token authentication.
Only the SHA-256 hash of each token is stored; the raw value is returned
once on creation and never persisted (mirrors JupyterHub's token model).

Guarded by _table_exists() so replaying this migration against a database
that already has the table is a no-op.
"""

import sqlalchemy as sa
from alembic import op

revision = "003_api_tokens"
down_revision = "002_namespace_permissions"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if _table_exists("api_tokens"):
        return

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        # SHA-256 hex digest — 64 chars, but 128 gives headroom for algorithm changes
        sa.Column("hashed_token", sa.String(128), unique=True, nullable=False),
        # First 4 chars of the raw token stored plaintext for display / prefix search
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("scopes", sa.JSON, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created", sa.DateTime, nullable=True),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("last_activity", sa.DateTime, nullable=True),
    )
    op.create_index("ix_api_tokens_hashed_token", "api_tokens", ["hashed_token"])
    op.create_index("ix_api_tokens_prefix", "api_tokens", ["prefix"])
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])


def downgrade() -> None:
    if _table_exists("api_tokens"):
        op.drop_table("api_tokens")
