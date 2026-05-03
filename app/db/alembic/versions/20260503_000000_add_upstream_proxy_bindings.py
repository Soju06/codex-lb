"""add upstream proxy bindings

Revision ID: 20260503_000000_add_upstream_proxy_bindings
Revises: 20260424_000000_merge_dashboard_session_ttl_and_request_log_heads
Create Date: 2026-05-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20260503_000000_add_upstream_proxy_bindings"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    add_account_proxy_url = not _has_column("accounts", "upstream_proxy_url_encrypted")
    add_account_proxy_group = not _has_column("accounts", "upstream_proxy_group")
    add_account_proxy_group_index = not _has_index("accounts", "ix_accounts_upstream_proxy_group")
    if add_account_proxy_url or add_account_proxy_group or add_account_proxy_group_index:
        with op.batch_alter_table("accounts") as batch_op:
            if add_account_proxy_url:
                batch_op.add_column(sa.Column("upstream_proxy_url_encrypted", sa.LargeBinary(), nullable=True))
            if add_account_proxy_group:
                batch_op.add_column(sa.Column("upstream_proxy_group", sa.String(), nullable=True))
            if add_account_proxy_group_index:
                batch_op.create_index("ix_accounts_upstream_proxy_group", ["upstream_proxy_group"])

    if not _has_column("dashboard_settings", "upstream_proxy_url_encrypted"):
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(sa.Column("upstream_proxy_url_encrypted", sa.LargeBinary(), nullable=True))

    if not _has_table("upstream_proxy_groups"):
        op.create_table(
            "upstream_proxy_groups",
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("proxy_url_encrypted", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("name"),
        )


def downgrade() -> None:
    if _has_table("upstream_proxy_groups"):
        op.drop_table("upstream_proxy_groups")
    if _has_column("dashboard_settings", "upstream_proxy_url_encrypted"):
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("upstream_proxy_url_encrypted")
    drop_account_proxy_url = _has_column("accounts", "upstream_proxy_url_encrypted")
    drop_account_proxy_group = _has_column("accounts", "upstream_proxy_group")
    drop_account_proxy_group_index = _has_index("accounts", "ix_accounts_upstream_proxy_group")
    if drop_account_proxy_url or drop_account_proxy_group or drop_account_proxy_group_index:
        with op.batch_alter_table("accounts") as batch_op:
            if drop_account_proxy_group_index:
                batch_op.drop_index("ix_accounts_upstream_proxy_group")
            if drop_account_proxy_group:
                batch_op.drop_column("upstream_proxy_group")
            if drop_account_proxy_url:
                batch_op.drop_column("upstream_proxy_url_encrypted")
