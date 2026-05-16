"""add account security work authorization flag

Revision ID: 20260425_000000_add_account_security_work_authorized
Revises: 20260424_000000_merge_dashboard_session_ttl_and_request_log_heads
Create Date: 2026-04-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260425_000000_add_account_security_work_authorized"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("accounts"):
        return

    columns = {column["name"] for column in inspector.get_columns("accounts")}
    if "security_work_authorized" not in columns:
        op.add_column(
            "accounts",
            sa.Column(
                "security_work_authorized",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("accounts"):
        return

    columns = {column["name"] for column in inspector.get_columns("accounts")}
    if "security_work_authorized" in columns:
        op.drop_column("accounts", "security_work_authorized")
