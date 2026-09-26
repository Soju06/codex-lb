"""add the request_logs indexes the slow log asked for (MySQL port branch)

Both come from the instance's slow log (`long_query_time = 0.5`, plans included),
where `request_logs` was the only table with genuine production offenders:

* ``idx_logs_facet_accounts (deleted_at, status, account_id)`` — the dashboard
  facet pager enumerated distinct ``account_id`` values with a recursive
  ``min()``; it ran as a full table scan and read 37.6 M rows across 129 calls.
  With this index the filter set is a covering ``ref`` scan.
* ``idx_logs_min_requested (deleted_at, request_kind, requested_at)`` — the
  earliest-request aggregate filtered ``request_kind NOT IN ('warmup',
  'limit_warmup')``, which the existing ``deleted_at`` index could not narrow,
  so every row in the range was read (61 calls, 12.9 s worst case).

Both are created with ``if_not_exists`` because the running instance already has
them (added online while investigating); on a fresh database they are new.

Revision ID: 20260926_030000_add_request_logs_facet_indexes
Revises: 20260926_010000_widen_account_status_enum
Create Date: 2026-09-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_indexes import create_mysql_index, is_mysql

# revision identifiers, used by Alembic.
revision = "20260926_030000_add_request_logs_facet_indexes"
down_revision = "20260926_010000_widen_account_status_enum"
branch_labels = None
depends_on = None

_INDEXES = (
    ("idx_logs_facet_accounts", ["deleted_at", "status", "account_id"]),
    ("idx_logs_min_requested", ["deleted_at", "request_kind", "requested_at"]),
)


def _drop_invalid_postgres_index(index_name: str) -> None:
    """Drop a leftover invalid index from an interrupted concurrent build.

    ``IF NOT EXISTS`` would silently accept the invalid index by name, leaving
    the facet probes without a usable index.
    """
    bind = op.get_bind()
    invalid = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = :name AND NOT i.indisvalid"
        ),
        {"name": index_name},
    ).first()
    if invalid:
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))


def upgrade() -> None:
    bind = op.get_bind()
    if is_mysql(bind):
        for name, columns in _INDEXES:
            create_mysql_index(
                bind,
                index_name=name,
                table_name="request_logs",
                columns_sql=", ".join(f"`{column}`" for column in columns),
            )
        return

    if bind.dialect.name == "postgresql":
        # A plain CREATE INDEX holds a lock that blocks request_logs inserts for
        # the whole build; request_logs is the hottest table, so the live-index
        # migrations on main build CONCURRENTLY for exactly this reason.
        with op.get_context().autocommit_block():
            for name, columns in _INDEXES:
                _drop_invalid_postgres_index(name)
                op.execute(
                    sa.text(
                        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                        f"ON request_logs ({', '.join(columns)})"
                    )
                )
        return

    for name, columns in _INDEXES:
        op.create_index(name, "request_logs", columns, unique=False, if_not_exists=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for name, _columns in _INDEXES:
                op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
        return
    for name, _columns in _INDEXES:
        op.drop_index(name, table_name="request_logs", if_exists=True)
