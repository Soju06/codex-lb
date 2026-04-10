"""add shared bootstrap token storage to dashboard settings

Revision ID: 20260409_010000_add_dashboard_settings_bootstrap_token
Revises: 20260409_000000_switch_sticky_threads_and_prefer_earlier_reset_defaults_to_true
Create Date: 2026-04-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260409_010000_add_dashboard_settings_bootstrap_token"
down_revision = "20260409_000000_switch_sticky_threads_and_prefer_earlier_reset_defaults_to_true"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "dashboard_settings"):
        return

    columns = _columns(bind, "dashboard_settings")
    if "bootstrap_token_encrypted" not in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(sa.Column("bootstrap_token_encrypted", sa.LargeBinary(), nullable=True))
    if "bootstrap_token_hash" not in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(sa.Column("bootstrap_token_hash", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "dashboard_settings"):
        return

    columns = _columns(bind, "dashboard_settings")
    if "bootstrap_token_hash" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("bootstrap_token_hash")
    if "bootstrap_token_encrypted" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("bootstrap_token_encrypted")
