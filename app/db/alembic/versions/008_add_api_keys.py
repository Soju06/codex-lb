"""add api_keys table and request_logs.api_key_id

Revision ID: 008_add_api_keys
Revises: 007_add_dashboard_settings_password
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "008_add_api_keys"
down_revision = "007_add_dashboard_settings_password"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("key_hash", sa.String(), nullable=False, unique=True),
            sa.Column("key_prefix", sa.String(), nullable=False),
            sa.Column("allowed_models", sa.Text(), nullable=True),
            sa.Column("weekly_token_limit", sa.Integer(), nullable=True),
            sa.Column("weekly_tokens_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("weekly_reset_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
        )

    existing_indexes = _indexes(bind, "api_keys")
    if "idx_api_keys_hash" not in existing_indexes:
        op.create_index("idx_api_keys_hash", "api_keys", ["key_hash"], unique=False)

    request_logs_columns = _columns(bind, "request_logs")
    if request_logs_columns and "api_key_id" not in request_logs_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.add_column(sa.Column("api_key_id", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    request_logs_columns = _columns(bind, "request_logs")
    if "api_key_id" in request_logs_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.drop_column("api_key_id")

    if _table_exists(bind, "api_keys"):
        existing_indexes = _indexes(bind, "api_keys")
        if "idx_api_keys_hash" in existing_indexes:
            op.drop_index("idx_api_keys_hash", table_name="api_keys")
        op.drop_table("api_keys")
