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

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260926_030000_add_request_logs_facet_indexes"
down_revision = "20260926_010000_widen_account_status_enum"
branch_labels = None
depends_on = None

_INDEXES = (
    ("idx_logs_facet_accounts", ["deleted_at", "status", "account_id"]),
    ("idx_logs_min_requested", ["deleted_at", "request_kind", "requested_at"]),
)


def upgrade() -> None:
    for name, columns in _INDEXES:
        op.create_index(name, "request_logs", columns, unique=False, if_not_exists=True)


def downgrade() -> None:
    for name, _columns in _INDEXES:
        op.drop_index(name, table_name="request_logs", if_exists=True)
