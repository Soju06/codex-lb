"""add codex session/conversation hashes to request_logs

Revision ID: 013_add_request_logs_codex_session_hashes
Revises: 012_add_import_without_overwrite_and_drop_accounts_email_unique
Create Date: 2026-02-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "013_add_request_logs_codex_session_hashes"
down_revision = "012_add_import_without_overwrite_and_drop_accounts_email_unique"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        if "codex_session_hash" not in columns:
            batch_op.add_column(sa.Column("codex_session_hash", sa.String(), nullable=True))
        if "codex_conversation_hash" not in columns:
            batch_op.add_column(sa.Column("codex_conversation_hash", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        if "codex_conversation_hash" in columns:
            batch_op.drop_column("codex_conversation_hash")
        if "codex_session_hash" in columns:
            batch_op.drop_column("codex_session_hash")
