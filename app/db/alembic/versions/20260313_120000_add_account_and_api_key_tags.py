"""add account and api key tags

Revision ID: 20260313_120000_add_account_and_api_key_tags
Revises: 20260312_120000_add_dashboard_upstream_stream_transport
Create Date: 2026-03-13 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260313_120000_add_account_and_api_key_tags"
down_revision = "20260312_120000_add_dashboard_upstream_stream_transport"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


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

    if not _table_exists(bind, "tags"):
        op.create_table(
            "tags",
            sa.Column("name", sa.String(), primary_key=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

    if not _table_exists(bind, "account_tags"):
        op.create_table(
            "account_tags",
            sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("tag_name", sa.String(), sa.ForeignKey("tags.name", ondelete="CASCADE"), primary_key=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

    if not _table_exists(bind, "api_key_tags"):
        op.create_table(
            "api_key_tags",
            sa.Column("api_key_id", sa.String(), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("tag_name", sa.String(), sa.ForeignKey("tags.name", ondelete="CASCADE"), primary_key=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

    account_tag_indexes = _indexes(bind, "account_tags")
    if "idx_account_tags_tag_name" not in account_tag_indexes:
        op.create_index("idx_account_tags_tag_name", "account_tags", ["tag_name"], unique=False)

    api_key_tag_indexes = _indexes(bind, "api_key_tags")
    if "idx_api_key_tags_tag_name" not in api_key_tag_indexes:
        op.create_index("idx_api_key_tags_tag_name", "api_key_tags", ["tag_name"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "api_key_tags"):
        api_key_tag_indexes = _indexes(bind, "api_key_tags")
        if "idx_api_key_tags_tag_name" in api_key_tag_indexes:
            op.drop_index("idx_api_key_tags_tag_name", table_name="api_key_tags")
        op.drop_table("api_key_tags")

    if _table_exists(bind, "account_tags"):
        account_tag_indexes = _indexes(bind, "account_tags")
        if "idx_account_tags_tag_name" in account_tag_indexes:
            op.drop_index("idx_account_tags_tag_name", table_name="account_tags")
        op.drop_table("account_tags")

    if _table_exists(bind, "tags"):
        tag_columns = _columns(bind, "tags")
        if {"name", "created_at"} <= tag_columns:
            op.drop_table("tags")
