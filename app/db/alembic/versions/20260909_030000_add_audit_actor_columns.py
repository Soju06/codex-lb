"""Add actor, target and severity columns to audit_logs.

Revision ID: 20260909_030000_add_audit_actor_columns
Revises: 20260909_020000_reproject_compat_admin_credentials
Create Date: 2026-09-09

The actor columns are a snapshot (no foreign key) so an audit row outlives the
account it names. Existing rows keep NULL actor/target columns and receive
``severity='info'`` through the server default.

On SQLite the ``timestamp`` text of rows written through the database default
(``CURRENT_TIMESTAMP``, second granularity) is padded once to the
microsecond form the ORM writes and binds, so time-window predicates and
same-second ordering compare consistently. Representation only; the instant
does not change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_030000_add_audit_actor_columns"
down_revision = "20260909_020000_reproject_compat_admin_credentials"
branch_labels = None
depends_on = None

_TABLE = "audit_logs"
_COLUMN_NAMES: tuple[str, ...] = (
    "actor_user_id",
    "actor_username",
    "actor_role_slug",
    "auth_method",
    "target_type",
    "target_id",
    "severity",
)
_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("idx_audit_logs_actor_user_id", ("actor_user_id",)),
    ("idx_audit_logs_target", ("target_type", "target_id")),
)


def _new_columns() -> tuple[sa.Column, ...]:
    """Fresh Column objects per call: a Column may be bound to one table only."""

    return (
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=True),
        sa.Column("actor_role_slug", sa.String(length=32), nullable=True),
        sa.Column("auth_method", sa.String(length=32), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default=sa.text("'info'")),
    )


def _columns(inspector: sa.Inspector) -> set[str]:
    if not inspector.has_table(_TABLE):
        return set()
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def _indexes(inspector: sa.Inspector) -> set[str]:
    if not inspector.has_table(_TABLE):
        return set()
    return {name for index in inspector.get_indexes(_TABLE) if isinstance(name := index.get("name"), str)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return
    existing_columns = _columns(inspector)
    missing = [column for column in _new_columns() if column.name not in existing_columns]
    if missing:
        with op.batch_alter_table(_TABLE) as batch_op:
            for column in missing:
                batch_op.add_column(column)
    existing_indexes = _indexes(sa.inspect(bind))
    for name, columns in _INDEXES:
        if name not in existing_indexes:
            op.create_index(name, _TABLE, list(columns))
    if bind.dialect.name == "sqlite":
        op.execute(sa.text("UPDATE audit_logs SET timestamp = timestamp || '.000000' WHERE timestamp NOT LIKE '%.%'"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return
    existing_indexes = _indexes(inspector)
    for name, _ in _INDEXES:
        if name in existing_indexes:
            op.drop_index(name, table_name=_TABLE)
    existing_columns = _columns(inspector)
    present = [name for name in _COLUMN_NAMES if name in existing_columns]
    if present:
        with op.batch_alter_table(_TABLE) as batch_op:
            for name in present:
                batch_op.drop_column(name)
