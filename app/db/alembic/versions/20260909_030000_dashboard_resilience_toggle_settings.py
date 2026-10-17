"""add dashboard-managed resilience toggle columns

Three nullable ``dashboard_settings`` BOOLEAN columns named after the
``Settings`` fields they take over: ``soft_drain_enabled``,
``deterministic_failover_enabled`` and ``circuit_breaker_enabled``. NULL means
"inherit": the deprecated ``CODEX_LB_*`` environment alias, then the code
default, keep applying until an operator sets a value in the dashboard
(configuration-tiers; slop-removal campaign 0908, C2-3).

Revision ID: 20260909_030000_dashboard_resilience_toggle_settings
Revises: 20260908_020000_merge_overflow_transport_heads
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260909_030000_dashboard_resilience_toggle_settings"
down_revision = "20260908_020000_merge_overflow_transport_heads"
branch_labels = None
depends_on = None

_SETTINGS_TABLE = "dashboard_settings"
_COLUMN_NAMES = (
    "soft_drain_enabled",
    "deterministic_failover_enabled",
    "circuit_breaker_enabled",
)


def _settings_columns() -> tuple[sa.Column, ...]:
    # Fresh Column objects per call: a Column binds to the table it is added to.
    return tuple(sa.Column(name, sa.Boolean(), nullable=True) for name in _COLUMN_NAMES)


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    missing_columns = [column for column in _settings_columns() if column.name not in existing_columns]
    if existing_columns and missing_columns:
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            for column in missing_columns:
                batch_op.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    present_columns = [name for name in _COLUMN_NAMES if name in existing_columns]
    if present_columns:
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            for column_name in present_columns:
                batch_op.drop_column(column_name)
