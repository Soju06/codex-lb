"""add plan_type snapshot to request_logs

Revision ID: 20260417_000000_add_request_log_plan_type
Revises: 20260415_160000_add_request_logs_response_lookup_index
Create Date: 2026-04-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260417_000000_add_request_log_plan_type"
down_revision = "20260415_160000_add_request_logs_response_lookup_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    columns = {column["name"] for column in inspector.get_columns("request_logs")}
    if "plan_type" in columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(sa.Column("plan_type", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    columns = {column["name"] for column in inspector.get_columns("request_logs")}
    if "plan_type" not in columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("plan_type")
