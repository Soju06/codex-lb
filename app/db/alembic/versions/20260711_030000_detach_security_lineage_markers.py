"""Allow security-lineage markers to outlive account rows.

Revision ID: 20260711_030000_detach_security_lineage_markers
Revises: 20260711_020000_add_sticky_session_security_lineage
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260711_030000_detach_security_lineage_markers"
down_revision = "20260711_020000_add_sticky_session_security_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("sticky_sessions"):
        return
    columns = {column["name"]: column for column in inspector.get_columns("sticky_sessions")}
    account_id = columns.get("account_id")
    if account_id is not None and not account_id.get("nullable", False):
        with op.batch_alter_table("sticky_sessions") as batch_op:
            batch_op.alter_column("account_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("sticky_sessions"):
        return
    columns = {column["name"]: column for column in inspector.get_columns("sticky_sessions")}
    account_id = columns.get("account_id")
    if account_id is not None and account_id.get("nullable", False):
        op.execute(sa.text("DELETE FROM sticky_sessions WHERE account_id IS NULL"))
        with op.batch_alter_table("sticky_sessions") as batch_op:
            batch_op.alter_column("account_id", existing_type=sa.String(), nullable=False)
