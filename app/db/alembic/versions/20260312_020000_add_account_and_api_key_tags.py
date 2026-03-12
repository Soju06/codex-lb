"""add account and api key tags

Revision ID: 20260312_020000_add_account_and_api_key_tags
Revises: 20260312_010000_merge_additional_usage_and_sticky_session_heads
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260312_020000_add_account_and_api_key_tags"
down_revision = "20260312_010000_merge_additional_usage_and_sticky_session_heads"
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

    if not _table_exists(bind, "tags"):
        op.create_table(
            "tags",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(), nullable=False, unique=True),
        )

    if not _table_exists(bind, "account_tags"):
        op.create_table(
            "account_tags",
            sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
            sa.UniqueConstraint("account_id", "tag_id", name="uq_account_tags_account_tag"),
        )

    if not _table_exists(bind, "api_key_tags"):
        op.create_table(
            "api_key_tags",
            sa.Column("api_key_id", sa.String(), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
            sa.UniqueConstraint("api_key_id", "tag_id", name="uq_api_key_tags_key_tag"),
        )

    existing_indexes = _indexes(bind, "tags")
    if "idx_tags_name" not in existing_indexes:
        op.create_index("idx_tags_name", "tags", ["name"])

    existing_indexes = _indexes(bind, "account_tags")
    if "idx_account_tags_account_id" not in existing_indexes:
        op.create_index("idx_account_tags_account_id", "account_tags", ["account_id"])
    if "idx_account_tags_tag_id" not in existing_indexes:
        op.create_index("idx_account_tags_tag_id", "account_tags", ["tag_id"])

    existing_indexes = _indexes(bind, "api_key_tags")
    if "idx_api_key_tags_key_id" not in existing_indexes:
        op.create_index("idx_api_key_tags_key_id", "api_key_tags", ["api_key_id"])
    if "idx_api_key_tags_tag_id" not in existing_indexes:
        op.create_index("idx_api_key_tags_tag_id", "api_key_tags", ["tag_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "api_key_tags"):
        existing_indexes = _indexes(bind, "api_key_tags")
        if "idx_api_key_tags_tag_id" in existing_indexes:
            op.drop_index("idx_api_key_tags_tag_id", table_name="api_key_tags")
        if "idx_api_key_tags_key_id" in existing_indexes:
            op.drop_index("idx_api_key_tags_key_id", table_name="api_key_tags")
        op.drop_table("api_key_tags")
    if _table_exists(bind, "account_tags"):
        existing_indexes = _indexes(bind, "account_tags")
        if "idx_account_tags_tag_id" in existing_indexes:
            op.drop_index("idx_account_tags_tag_id", table_name="account_tags")
        if "idx_account_tags_account_id" in existing_indexes:
            op.drop_index("idx_account_tags_account_id", table_name="account_tags")
        op.drop_table("account_tags")
    if _table_exists(bind, "tags"):
        existing_indexes = _indexes(bind, "tags")
        if "idx_tags_name" in existing_indexes:
            op.drop_index("idx_tags_name", table_name="tags")
        op.drop_table("tags")
