"""add retry-circuit admission claim lease

Revision ID: 20260829_000000_add_retry_circuit_admission_claim_marker
Revises: 20260830_000000_add_quota_warmup_claim_expiry
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260829_000000_add_retry_circuit_admission_claim_marker"
down_revision = "20260830_000000_add_quota_warmup_claim_expiry"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_retry_circuits"
_COLUMNS = {
    "admission_claimed_at_epoch": sa.Float(),
    "admission_claimed_generation": sa.Integer(),
    "admission_claimed_until_epoch": sa.Float(),
}


def _active_claim_statement(dialect_name: str) -> sa.TextClause:
    if dialect_name == "postgresql":
        database_now_epoch = "EXTRACT(EPOCH FROM clock_timestamp())"
    elif dialect_name == "sqlite":
        database_now_epoch = "((julianday('now') - 2440587.5) * 86400.0)"
    else:
        raise RuntimeError(f"retry-circuit admission claim migration unsupported for dialect={dialect_name!r}")
    return sa.text(f"SELECT 1 FROM {_TABLE} WHERE admission_claimed_until_epoch > {database_now_epoch} LIMIT 1")


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def _acquire_downgrade_lock(bind) -> None:
    """Serialize claim writes with the receipt check and marker drop."""
    if bind.dialect.name == "postgresql":
        # ACCESS EXCLUSIVE conflicts with the UPDATE used by replay claims and
        # remains held through the surrounding Alembic transaction.
        bind.execute(sa.text(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        # SQLite has no table lock; BEGIN IMMEDIATE takes the database writer
        # slot before the receipt read and holds it through batch DDL.
        bind.execute(sa.text("BEGIN IMMEDIATE"))


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        existing = _columns(bind)
        for name, column_type in _COLUMNS.items():
            if name not in existing:
                batch_op.add_column(sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    if not existing.intersection(_COLUMNS):
        return
    _acquire_downgrade_lock(bind)
    # Re-read under the write/table lock so a concurrent migration cannot
    # change the marker set between the initial existence check and DDL.
    existing = _columns(bind)
    if not existing.intersection(_COLUMNS):
        return
    # The nullable receipt columns are the durable fence for an in-flight
    # replay; dropping them while a lease is live would allow a second replay
    # to be admitted for the same generation. Refuse before any DDL or version
    # stamping, then allow a complete downgrade once every lease has expired.
    active_claim = None
    if "admission_claimed_until_epoch" in existing:
        active_claim = bind.execute(_active_claim_statement(bind.dialect.name)).first()
    if active_claim is not None:
        raise RuntimeError(
            "cannot downgrade retry-circuit admission claim marker migration while active receipts exist"
        )
    with op.batch_alter_table(_TABLE) as batch_op:
        for name in _COLUMNS:
            if name in existing:
                batch_op.drop_column(name)
