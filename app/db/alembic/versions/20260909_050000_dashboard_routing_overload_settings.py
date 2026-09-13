"""dashboard-managed routing weights and overload isolation

Revision ID: 20260909_050000_dashboard_routing_overload_settings
Revises: 20260909_040000_dashboard_timeout_settings
Create Date: 2026-09-09

Adds the nullable ``dashboard_settings`` columns for the routing/overload
tunables that were environment-only (C2-2 of the slop-removal campaign):
``proxy_overload_isolation_seconds``, ``proxy_account_error_rate_weighting_enabled``,
``proxy_account_inflight_penalty_pct``, ``proxy_account_lease_token_weight`` and
``proxy_account_lease_ttl_seconds``. NULL inherits the process environment
value (or the code default) at read time; the first-boot seed and this
migration never copy the environment value into the row.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260909_050000_dashboard_routing_overload_settings"
down_revision = "20260909_040000_dashboard_timeout_settings"
branch_labels = None
depends_on = None

_TABLE = "dashboard_settings"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("proxy_overload_isolation_seconds", sa.Integer()),
    ("proxy_account_error_rate_weighting_enabled", sa.Boolean()),
    ("proxy_account_inflight_penalty_pct", sa.Float()),
    ("proxy_account_lease_token_weight", sa.Float()),
    ("proxy_account_lease_ttl_seconds", sa.Float()),
)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, _TABLE)
    if not existing:
        return
    missing = [(name, column_type) for name, column_type in _COLUMNS if name not in existing]
    if not missing:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        for name, column_type in missing:
            batch_op.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, _TABLE)
    present = [name for name, _ in _COLUMNS if name in existing]
    if not present:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        for name in present:
            batch_op.drop_column(name)
