"""dashboard-managed per-model context window overrides

Revision ID: 20260909_110000_model_context_window_overrides
Revises: 20260909_080000_dashboard_stream_bridge_budgets
Create Date: 2026-09-09

Adds the ``model_context_window_overrides`` table (M4 of the slop-removal
campaign): one row per model slug whose reported context window an operator
overrides from the dashboard. A row wins over the
``CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES`` entry for the same slug; slugs
without a row keep inheriting the environment entry (or have no override).
The migration never copies the environment dict into rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_110000_model_context_window_overrides"
down_revision = "20260909_080000_dashboard_stream_bridge_budgets"
branch_labels = None
depends_on = None

_TABLE = "model_context_window_overrides"


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("slug"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
