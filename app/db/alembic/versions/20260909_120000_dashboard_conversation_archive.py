"""add dashboard-managed conversation archive toggle column

One nullable ``dashboard_settings`` BOOLEAN column named after the ``Settings``
field it takes over: ``conversation_archive_enabled``. NULL means "inherit":
the deprecated ``CODEX_LB_CONVERSATION_ARCHIVE_ENABLED`` environment alias,
then the code default (off), keep applying until an operator sets a value in
the dashboard (configuration-tiers; slop-removal campaign 0908, M5).

Revision ID: 20260909_120000_dashboard_conversation_archive
Revises: 20260909_090000_dashboard_background_job_toggles
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260909_120000_dashboard_conversation_archive"
down_revision = "20260909_090000_dashboard_background_job_toggles"
branch_labels = None
depends_on = None

_SETTINGS_TABLE = "dashboard_settings"
_COLUMN_NAME = "conversation_archive_enabled"


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    if existing_columns and _COLUMN_NAME not in existing_columns:
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN_NAME, sa.Boolean(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN_NAME in _column_names(bind, _SETTINGS_TABLE):
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN_NAME)
