"""add request kind column

Revision ID: 20260427_130000_add_warmup_model_and_request_kind
Revises: 20260424_000000_merge_dashboard_session_ttl_and_request_log_heads
Create Date: 2026-04-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260427_130000_add_warmup_model_and_request_kind"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("request_logs"):
        request_log_columns = {column["name"] for column in inspector.get_columns("request_logs")}
        if "request_kind" not in request_log_columns:
            with op.batch_alter_table("request_logs") as batch_op:
                batch_op.add_column(
                    sa.Column("request_kind", sa.String(), nullable=True, server_default=sa.text("'normal'"))
                )
            op.execute(
                sa.text(
                    """
                    UPDATE request_logs
                    SET request_kind = 'normal'
                    WHERE request_kind IS NULL OR request_kind = ''
                    """
                )
            )
            with op.batch_alter_table("request_logs") as batch_op:
                batch_op.alter_column("request_kind", existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("request_logs"):
        request_log_columns = {column["name"] for column in inspector.get_columns("request_logs")}
        if "request_kind" in request_log_columns:
            with op.batch_alter_table("request_logs") as batch_op:
                batch_op.drop_column("request_kind")
