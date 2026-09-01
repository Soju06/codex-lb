"""repair schema objects skipped by the persisted recovery merge stamp

Revision ID: 20260901_000000_repair_persisted_schema_drift
Revises: 20260828_020000_merge_http_bridge_recovery_heads
Create Date: 2026-09-01

The recovery merge revision was deployed to a live database before the
corresponding re-parented migrations were present in the image.  That left the
Alembic stamp at the merge revision while the index and lease column were
absent.  Keep this repair idempotent so a rolling deployment can safely bring
such a database to the real schema without changing existing data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260901_000000_repair_persisted_schema_drift"
down_revision = "20260828_020000_merge_http_bridge_recovery_heads"
branch_labels = None
depends_on = None

_OPERATIONS_TABLE = "http_bridge_operations"
_OPERATIONS_INDEX = "idx_http_bridge_operations_session_state_created"
_QUOTA_TABLE = "quota_planner_decisions"
_LEASE_COLUMN = "lease_expires_at"
_LEGACY_CLAIM_LEASE_WINDOW_SECONDS = 7200


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def _has_index(connection: Connection, table_name: str, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in sa.inspect(connection).get_indexes(table_name))


def _quota_columns(connection: Connection) -> set[str]:
    return {str(column["name"]) for column in sa.inspect(connection).get_columns(_QUOTA_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, _OPERATIONS_TABLE) and not _has_index(bind, _OPERATIONS_TABLE, _OPERATIONS_INDEX):
        op.create_index(
            _OPERATIONS_INDEX,
            _OPERATIONS_TABLE,
            ["session_id", "state", "created_at"],
            unique=False,
        )

    if _has_table(bind, _QUOTA_TABLE):
        columns = _quota_columns(bind)
        if _LEASE_COLUMN not in columns:
            with op.batch_alter_table(_QUOTA_TABLE) as batch_op:
                batch_op.add_column(sa.Column(_LEASE_COLUMN, sa.DateTime(), nullable=True))

        if bind.dialect.name == "postgresql":
            lease_expr = (
                "COALESCE(executed_at, created_at, TIMESTAMP '1970-01-01 00:00:00') "
                f"+ make_interval(secs => {_LEGACY_CLAIM_LEASE_WINDOW_SECONDS})"
            )
        else:
            lease_expr = (
                "(strftime('%Y-%m-%d %H:%M:%f', "
                "COALESCE(executed_at, created_at, '1970-01-01 00:00:00'), "
                f"'+{_LEGACY_CLAIM_LEASE_WINDOW_SECONDS} seconds') || '000')"
            )
        op.execute(
            sa.text(
                f"UPDATE {_QUOTA_TABLE} "
                f"SET {_LEASE_COLUMN} = {lease_expr} "
                "WHERE action = 'warmup' AND status = 'executing' "
                f"AND {_LEASE_COLUMN} IS NULL"
            )
        )


def downgrade() -> None:
    """Keep objects owned by the parent migrations intact on downgrade.

    Both the quota lease column and the operations index belong to revisions
    earlier in this graph.  This repair only fills them in for databases that
    were stamped past those revisions before the DDL reached the image, so a
    downgrade must not remove objects the parent revision still requires.
    """
