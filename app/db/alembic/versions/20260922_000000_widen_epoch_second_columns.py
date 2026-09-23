"""Widen epoch-second integer columns so MySQL and PostgreSQL can hold real values.

Revision ID: 20260922_000000_widen_epoch_second_columns
Revises: 20260913_000000_add_oidc_provider_flow
Create Date: 2026-09-22

Epoch seconds are 64-bit quantities. SQLite's ``INTEGER`` has always stored them,
but MySQL's ``INT`` and PostgreSQL's ``INTEGER`` are 32-bit and reject anything
past 2147483647 -- and these columns take their values from upstream payloads, so
an implausibly large ``reset_at`` used to abort the whole account write on MySQL
(``DataError (1264) Out of range value for column 'reset_at'``) instead of being
stored and ignored by the window math.

Fresh databases now create every one of these columns as ``BIGINT`` (see the
schema-type policy and the migration that first creates each table); this
migration widens databases installed before that. SQLite is skipped: its
``INTEGER`` is already 64-bit and rebuilding the tables would gain nothing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260922_000000_widen_epoch_second_columns"
down_revision = "20260918_000000_merge_scim_and_overflow_heads"
branch_labels = None
depends_on = None

# (table, column, nullable) for every column that stores epoch seconds.
_EPOCH_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("accounts", "reset_at", True),
    ("accounts", "blocked_at", True),
    ("usage_history", "reset_at", True),
    ("additional_usage_history", "reset_at", True),
    ("account_limit_warmups", "reset_at", False),
    ("quota_window_observations", "primary_reset_at", True),
    ("quota_window_observations", "secondary_reset_at", True),
)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _alter_epoch_columns(*, to_big_integer: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    for table_name, column_name, nullable in _EPOCH_COLUMNS:
        if column_name not in _columns(bind, table_name):
            continue
        existing_type, new_type = (sa.Integer(), sa.BigInteger()) if to_big_integer else (sa.BigInteger(), sa.Integer())
        op.alter_column(
            table_name,
            column_name,
            existing_type=existing_type,
            type_=new_type,
            existing_nullable=nullable,
        )


def upgrade() -> None:
    _alter_epoch_columns(to_big_integer=True)


def downgrade() -> None:
    # Narrowing back is only safe while no stored value exceeds the 32-bit range;
    # the inverse is offered for completeness, as the other migrations do.
    _alter_epoch_columns(to_big_integer=False)
