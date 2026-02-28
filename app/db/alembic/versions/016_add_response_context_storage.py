"""add response context storage tables

Revision ID: 016_add_response_context_storage
Revises: 015_add_dashboard_settings_global_model_force
Create Date: 2026-02-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "016_add_response_context_storage"
down_revision = "015_add_dashboard_settings_global_model_force"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "response_context"):
        op.create_table(
            "response_context",
            sa.Column("response_id", sa.String(), primary_key=True, nullable=False),
            sa.Column("api_key_id", sa.String(), nullable=True),
            sa.Column("output_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )

    if not _table_exists(bind, "response_context_items"):
        op.create_table(
            "response_context_items",
            sa.Column("item_id", sa.String(), primary_key=True, nullable=False),
            sa.Column("response_id", sa.String(), nullable=False),
            sa.Column("api_key_id", sa.String(), nullable=True),
            sa.Column("item_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["response_id"],
                ["response_context.response_id"],
                name="fk_response_context_items_response_id",
                ondelete="CASCADE",
            ),
        )

    response_indexes = _indexes(bind, "response_context")
    if "idx_response_context_api_key" not in response_indexes:
        op.create_index("idx_response_context_api_key", "response_context", ["api_key_id"])
    if "idx_response_context_expires_at" not in response_indexes:
        op.create_index("idx_response_context_expires_at", "response_context", ["expires_at"])

    item_indexes = _indexes(bind, "response_context_items")
    if "idx_response_context_items_response_id" not in item_indexes:
        op.create_index("idx_response_context_items_response_id", "response_context_items", ["response_id"])
    if "idx_response_context_items_api_key" not in item_indexes:
        op.create_index("idx_response_context_items_api_key", "response_context_items", ["api_key_id"])
    if "idx_response_context_items_expires_at" not in item_indexes:
        op.create_index("idx_response_context_items_expires_at", "response_context_items", ["expires_at"])


def downgrade() -> None:
    # Intentionally no-op to avoid destructive table rebuilds on SQLite.
    return

