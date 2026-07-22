"""Persist security-work classification on root sticky sessions.

Revision ID: 20260711_020000_add_sticky_session_security_lineage
Revises: 20260711_010000_reconcile_security_lineage_schema
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260711_020000_add_sticky_session_security_lineage"
down_revision = "20260711_010000_reconcile_security_lineage_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("sticky_sessions"):
        return
    columns = {column["name"] for column in inspector.get_columns("sticky_sessions")}
    if "requires_security_work_authorized" not in columns:
        with op.batch_alter_table("sticky_sessions") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "requires_security_work_authorized",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("sticky_sessions"):
        return
    columns = {column["name"] for column in inspector.get_columns("sticky_sessions")}
    if "requires_security_work_authorized" in columns:
        with op.batch_alter_table("sticky_sessions") as batch_op:
            batch_op.drop_column("requires_security_work_authorized")
