"""dashboard-managed Codex HTTP-bridge session prewarm

Revision ID: 20260909_100000_dashboard_codex_prewarm
Revises: 20260909_070000_automation_run_claim_budget
Create Date: 2026-09-09

Adds the nullable ``dashboard_settings.http_responses_session_bridge_codex_prewarm_enabled``
column (M3 of the slop-removal campaign). NULL inherits the deprecated
``CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED`` environment
alias (then the code default, off) at read time; the first-boot seed and this
migration never copy the environment value into the row.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260909_100000_dashboard_codex_prewarm"
down_revision = "20260909_070000_automation_run_claim_budget"
branch_labels = None
depends_on = None

_TABLE = "dashboard_settings"
_COLUMN = "http_responses_session_bridge_codex_prewarm_enabled"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    existing = _columns(op.get_bind(), _TABLE)
    if not existing or _COLUMN in existing:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Boolean(), nullable=True))


def downgrade() -> None:
    existing = _columns(op.get_bind(), _TABLE)
    if _COLUMN not in existing:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
