"""Add continuity-abandonment markers to durable HTTP bridge sessions.

Revision ID: 20260911_060000_add_bridge_session_continuity_abandonment
Revises: 20260911_030000_add_local_login_policy
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260911_060000_add_bridge_session_continuity_abandonment"
down_revision = "20260911_030000_add_local_login_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Mirrors sticky_sessions' pair exactly: a scope-only marker is the new
    # reader's tombstone, while a timestamp with NULL scope abandons globally.
    # Both stay NULL on every existing row, so a rolling deploy keeps treating
    # account_id as hard ownership until a writer retires a specific owner.
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("http_bridge_sessions")}
    with op.batch_alter_table("http_bridge_sessions") as batch_op:
        if "continuity_abandoned_at" not in columns:
            batch_op.add_column(sa.Column("continuity_abandoned_at", sa.DateTime(timezone=True), nullable=True))
        if "continuity_abandonment_scope" not in columns:
            batch_op.add_column(sa.Column("continuity_abandonment_scope", sa.String(32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("http_bridge_sessions")}
    with op.batch_alter_table("http_bridge_sessions") as batch_op:
        if "continuity_abandonment_scope" in columns:
            batch_op.drop_column("continuity_abandonment_scope")
        if "continuity_abandoned_at" in columns:
            batch_op.drop_column("continuity_abandoned_at")
